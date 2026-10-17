"""Configuration is read from the environment; Compose loads .env."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field


class ConfigError(ValueError):
    """A user-readable error that never contains configuration values."""


def _required(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise ConfigError(f"Не задан обязательный параметр {key}.")
    return value


def _snowflake(value: str, key: str) -> int:
    if not re.fullmatch(r"[0-9]{1,20}", value) or not 0 < int(value) < 2**64:
        raise ConfigError(f"{key}: ожидается положительный Discord ID (до 64 бит).")
    return int(value)


@dataclass(frozen=True, slots=True)
class Config:
    discord_token: str = field(repr=False)
    telegram_token: str = field(repr=False)
    telegram_chat_id: int
    guild_id: int
    channel_ids: frozenset[int]

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Config":
        discord_token = _required(env, "DISCORD_BOT_TOKEN")
        # Validate characters rather than a particular Discord token length/version.
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", discord_token):
            raise ConfigError("DISCORD_BOT_TOKEN: недопустимые символы в токене.")
        telegram_token = _required(env, "TELEGRAM_BOT_TOKEN")
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", telegram_token):
            raise ConfigError("TELEGRAM_BOT_TOKEN: ожидается токен BotFather вида число:секрет.")
        chat = _required(env, "TELEGRAM_CHAT_ID")
        if not re.fullmatch(r"-?[0-9]{1,19}", chat) or not 0 < abs(int(chat)) < 2**63:
            raise ConfigError(
                "TELEGRAM_CHAT_ID: ожидается ненулевой целый ID, включая отрицательный."
            )
        guild_id = _snowflake(_required(env, "DISCORD_GUILD_ID"), "DISCORD_GUILD_ID")
        raw_channels = _required(env, "DISCORD_VOICE_CHANNEL_IDS").split(",")
        channels = frozenset(
            _snowflake(value.strip(), "DISCORD_VOICE_CHANNEL_IDS") for value in raw_channels
        )
        return cls(discord_token, telegram_token, int(chat), guild_id, channels)
