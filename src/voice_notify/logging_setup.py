"""JSON logs with redaction applied after formatting, including tracebacks."""

import json
import logging
import re
from datetime import UTC, datetime


class SafeJsonFormatter(logging.Formatter):
    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.secrets = tuple(value for value in secrets if value)

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if record.exc_info:
            message += "\n" + self.formatException(record.exc_info)
        for secret in self.secrets:
            message = message.replace(secret, "[REDACTED]")
        message = re.sub(r"bot[0-9]+:[A-Za-z0-9_-]+", "bot[REDACTED]", message)
        return json.dumps(
            {
                "time": datetime.now(UTC).isoformat(timespec="seconds"),
                "level": record.levelname,
                "logger": record.name,
                "message": message,
            },
            ensure_ascii=False,
        )


def configure_logging(secrets: tuple[str, ...] = ()) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(SafeJsonFormatter(secrets))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    # HTTP DEBUG logs can contain request headers/payloads. Do not enable them.
    logging.getLogger("discord.http").setLevel(logging.WARNING)
