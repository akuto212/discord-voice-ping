import asyncio
import contextlib
import logging
import os
import signal

import aiohttp
import discord

from .config import Config, ConfigError
from .discord_client import VoiceClient
from .logging_setup import configure_logging
from .telegram import TelegramSender

log = logging.getLogger(__name__)


async def serve(config: Config) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    try:
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=2), trust_env=False
        ) as session:
            sender = TelegramSender(session, config.telegram_token, config.telegram_chat_id)
            async with VoiceClient(config, sender) as client:
                gateway = asyncio.create_task(client.start(config.discord_token), name="discord")
                worker = asyncio.create_task(sender.run(), name="telegram")
                stopping = asyncio.create_task(stop.wait(), name="stop")
                try:
                    done, _ = await asyncio.wait(
                        (gateway, worker, stopping, client.failure),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in done:
                        task.result()
                    if worker in done:
                        raise RuntimeError("Telegram worker unexpectedly stopped")
                finally:
                    log.info("shutdown: остановка Discord и завершение очереди (до 10 с)")
                    client.tracker.pause()
                    try:
                        await asyncio.wait_for(client.close(), timeout=5)
                    finally:
                        gateway.cancel()
                        stopping.cancel()
                        await asyncio.gather(gateway, stopping, return_exceptions=True)
                        if not worker.done():
                            with contextlib.suppress(TimeoutError):
                                await asyncio.wait_for(sender.queue.join(), timeout=10)
                        worker.cancel()
                        await asyncio.gather(worker, return_exceptions=True)
                        if not client.failure.done():
                            client.failure.cancel()
                        else:
                            client.failure.exception()
    finally:
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(signum)


def main() -> None:
    configure_logging()
    try:
        config = Config.from_env(os.environ)
    except ConfigError as error:
        log.error("configuration_error: %s", error)
        raise SystemExit(2) from None
    configure_logging((config.discord_token, config.telegram_token))
    try:
        asyncio.run(serve(config))
    except ConfigError as error:
        log.error("configuration_error: %s", error)
        raise SystemExit(2) from None
    except discord.LoginFailure:
        log.error("discord_login_failed: проверьте DISCORD_BOT_TOKEN")
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        pass
    except Exception:
        log.exception("fatal_error: приложение завершилось; Compose выполнит перезапуск")
        raise SystemExit(1) from None
