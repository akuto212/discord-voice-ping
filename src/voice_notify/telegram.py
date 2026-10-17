"""One bounded worker sends notifications independently of Discord events."""

import asyncio
import html
import json
import logging
import time
import unicodedata
from dataclasses import dataclass, field

import aiohttp

log = logging.getLogger(__name__)
QUEUE_SIZE = 100
MAX_AGE_SECONDS = 120
MAX_RETRY_AFTER = 30


def safe_name(value: str) -> str:
    # A name is one line. Remove controls/bidi overrides and break automatic @mentions.
    value = " ".join(value.split())
    value = "".join(c for c in value if unicodedata.category(c) not in {"Cc", "Cf", "Cs"})
    value = value[:200] or "—"
    return html.escape(value.replace("@", "@\u200b"), quote=True)


@dataclass(frozen=True, slots=True)
class Notification:
    user_name: str
    channel_name: str
    guild_name: str
    guild_id: int
    channel_id: int
    created_at: float = field(default_factory=time.monotonic)

    def render(self) -> str:
        return (
            "🔊 <b>В голосовом канале появился участник!</b>\n\n"
            f"👤 Пользователь: <code>{safe_name(self.user_name)}</code>\n"
            f"🎧 Канал: <code>{safe_name(self.channel_name)}</code>\n"
            f"🖥 Сервер: <code>{safe_name(self.guild_name)}</code>\n\n"
            f'<a href="https://discord.com/channels/{self.guild_id}/{self.channel_id}">'
            "🔗 Открыть голосовой канал</a>"
        )


class TelegramSender:
    def __init__(self, session: aiohttp.ClientSession, token: str, chat_id: int) -> None:
        self.session = session
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = chat_id
        self.queue: asyncio.Queue[Notification] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._next_send = 0.0
        self._send_interval = 3.1 if chat_id < 0 else 1.0

    def enqueue(self, notification: Notification) -> None:
        try:
            self.queue.put_nowait(notification)
        except asyncio.QueueFull:
            log.warning(
                "telegram_queue_full: уведомление отброшено; channel_id=%d", notification.channel_id
            )

    async def send(self, notification: Notification) -> None:
        payload = {
            "chat_id": self.chat_id,
            "text": notification.render(),
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        for attempt in range(2):
            delay = self._next_send - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            if time.monotonic() - notification.created_at > MAX_AGE_SECONDS:
                log.warning("telegram_expired: устаревшее уведомление отброшено")
                return
            try:
                # Never follow redirects: the URL itself contains the Bot API token.
                async with self.session.post(
                    self._url,
                    json=payload,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=10, connect=5),
                ) as response:
                    # A broken upstream must not allocate an unbounded response body.
                    body = await response.content.read(65537)
                    if len(body) > 65536:
                        log.error("telegram_invalid_response: превышен размер ответа")
                        return
                    data = json.loads(body)
                    if not isinstance(data, dict):
                        raise ValueError("Expected object")
                    code = data.get("error_code", response.status)
                    if response.status == 200 and data.get("ok") is True:
                        log.info("telegram_sent: channel_id=%d", notification.channel_id)
                        return
                    if code == 429:
                        parameters = data.get("parameters")
                        retry_after = (
                            parameters.get("retry_after") if isinstance(parameters, dict) else None
                        )
                        if type(retry_after) is int and 0 < retry_after <= MAX_RETRY_AFTER:
                            self._next_send = time.monotonic() + retry_after
                            if attempt == 0:
                                log.warning(
                                    "telegram_rate_limit: одна повторная попытка через %d с",
                                    retry_after,
                                )
                                continue
                    # Do not log API descriptions or response bodies: they are untrusted.
                    log.error(
                        "telegram_rejected: HTTP %d; проверьте токен, Chat ID и права",
                        response.status,
                    )
                    return
            except (aiohttp.ClientError, TimeoutError, ValueError):
                # Retrying an ambiguous result can deliver the same message twice.
                log.error("telegram_send_failed: сеть, таймаут или некорректный ответ; без повтора")
                return

    async def run(self) -> None:
        while True:
            notification = await self.queue.get()
            try:
                # Telegram permits 1/s privately and at most 20/min in a group.
                await self.send(notification)
            finally:
                self._next_send = max(self._next_send, time.monotonic() + self._send_interval)
                self.queue.task_done()
