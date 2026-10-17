import asyncio
import contextlib
import time
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from aiohttp import web

from voice_notify.telegram import QUEUE_SIZE, Notification, TelegramSender, safe_name


@pytest.fixture
def notification():
    return Notification("Александр", "Общий", "Сервер", 100, 200)


@pytest.fixture
async def api():
    """Real HTTP transport to loopback only; no external tokens or requests."""
    app = web.Application()
    responses = []
    requests = []

    async def handler(request):
        requests.append(await request.json())
        response = responses.pop(0) if responses else web.json_response({"ok": True})
        return response

    app.router.add_post("/sendMessage", handler)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    server = await asyncio.get_running_loop().create_server(runner.server, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with aiohttp.ClientSession() as session:
        sender = TelegramSender(session, "123:fake", -100123)
        sender._url = f"http://127.0.0.1:{port}/sendMessage"
        yield sender, responses, requests
    server.close()
    await server.wait_closed()
    await runner.cleanup()


async def test_successful_send_payload(api, notification):
    sender, _, requests = api
    await sender.send(notification)
    assert len(requests) == 1
    assert requests[0]["chat_id"] == -100123
    assert requests[0]["parse_mode"] == "HTML"
    assert requests[0]["link_preview_options"] == {"is_disabled": True}
    assert 'href="https://discord.com/channels/100/200"' in requests[0]["text"]


def test_untrusted_names_cannot_inject_html_or_mentions():
    value = '<a href="tg://user?id=1">@username</a> & <b>hello</b>\n\u202eevil'
    rendered = Notification(value, value, value, 100, 200).render()
    assert rendered.count("<a href=") == 1
    assert "&lt;a href=&quot;tg://user?id=1&quot;&gt;" in rendered
    assert "@username" not in rendered
    assert "@\u200busername" in rendered
    assert "\u202e" not in rendered
    assert "\n" not in safe_name(value)
    assert len(safe_name("a" * 10000)) == 200
    assert safe_name("\x00") == "—"


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 503])
async def test_errors_are_not_retried_or_logged_verbatim(api, notification, status, caplog):
    sender, responses, requests = api
    responses.append(
        web.json_response({"ok": False, "description": "PRIVATE_SECRET"}, status=status)
    )
    await sender.send(notification)
    assert len(requests) == 1
    assert "PRIVATE_SECRET" not in caplog.text
    assert "telegram_rejected" in caplog.text


async def test_429_retries_only_once(api, notification, monkeypatch):
    sender, responses, requests = api
    responses.extend(
        [
            web.json_response(
                {"ok": False, "error_code": 429, "parameters": {"retry_after": 1}}, status=429
            ),
            web.json_response(
                {"ok": False, "error_code": 429, "parameters": {"retry_after": 1}}, status=429
            ),
        ]
    )
    sleep = AsyncMock()
    monkeypatch.setattr("voice_notify.telegram.asyncio.sleep", sleep)
    await sender.send(notification)
    assert len(requests) == 2
    assert sleep.await_count == 1


@pytest.mark.parametrize("retry_after", [0, -1, 31, "1", True, None])
async def test_invalid_or_excessive_retry_delay_is_not_used(api, notification, retry_after):
    sender, responses, requests = api
    responses.append(
        web.json_response(
            {"ok": False, "error_code": 429, "parameters": {"retry_after": retry_after}}, status=429
        )
    )
    await sender.send(notification)
    assert len(requests) == 1


@pytest.mark.parametrize("body", ["not json", "[]", "null", "x" * 70000])
async def test_invalid_response_does_not_crash(api, notification, body):
    sender, responses, requests = api
    responses.append(web.Response(text=body))
    await sender.send(notification)
    assert len(requests) == 1


async def test_redirects_are_not_followed(api, notification):
    sender, responses, requests = api
    responses.append(web.Response(status=307, headers={"Location": "/sendMessage"}, text="{}"))
    await sender.send(notification)
    assert len(requests) == 1


@pytest.mark.parametrize("error", [aiohttp.ClientConnectionError("PRIVATE_TOKEN"), TimeoutError()])
async def test_network_and_timeout_have_no_ambiguous_retry(notification, error, caplog):
    session = MagicMock()
    session.post.side_effect = error
    sender = TelegramSender(session, "123:fake", 123)
    await sender.send(notification)
    assert session.post.call_count == 1
    assert "PRIVATE_TOKEN" not in caplog.text
    assert "telegram_send_failed" in caplog.text


async def test_worker_survives_failed_delivery(api, notification):
    sender, responses, requests = api
    responses.append(web.json_response({"ok": False}, status=500))
    sender.enqueue(notification)
    sender.enqueue(notification)
    worker = asyncio.create_task(sender.run())
    try:
        await asyncio.wait_for(sender.queue.join(), timeout=5)
        assert len(requests) == 2
        assert not worker.done()
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker


def test_queue_is_bounded_without_blocking(notification, caplog):
    sender = TelegramSender(MagicMock(), "123:fake", 123)
    for _ in range(QUEUE_SIZE + 10):
        sender.enqueue(notification)
    assert sender.queue.qsize() == QUEUE_SIZE
    assert "telegram_queue_full" in caplog.text


async def test_expired_notifications_are_dropped(api):
    sender, _, requests = api
    await sender.send(Notification("a", "b", "c", 100, 200, time.monotonic() - 121))
    assert not requests


async def test_slow_telegram_does_not_block_discord():
    # State mutation and enqueue finish while the worker is still in network I/O.
    from voice_notify.state import OccupancyTracker

    started = asyncio.Event()
    release = asyncio.Event()
    sender = TelegramSender(MagicMock(), "123:fake", 123)

    async def slow_send(_):
        started.set()
        await release.wait()

    sender.send = slow_send
    worker = asyncio.create_task(sender.run())
    try:
        tracker = OccupancyTracker(frozenset({200, 300}))
        tracker.synchronize({200: set(), 300: set()})
        assert tracker.update(1, 200)
        sender.enqueue(Notification("a", "b", "c", 100, 200))
        await asyncio.wait_for(started.wait(), timeout=1)
        assert tracker.update(2, 300)
        sender.enqueue(Notification("a", "b", "c", 100, 300))
        assert sender.queue.qsize() == 1
    finally:
        release.set()
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
