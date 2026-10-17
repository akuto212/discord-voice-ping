import pytest

from voice_notify.config import Config


@pytest.fixture
def env():
    return {
        "DISCORD_BOT_TOKEN": "fake.discord.token",
        "TELEGRAM_BOT_TOKEN": "12345:fake_telegram_token",
        "TELEGRAM_CHAT_ID": "-1001234567890",
        "DISCORD_GUILD_ID": "100",
        "DISCORD_VOICE_CHANNEL_IDS": "200,300",
    }


@pytest.fixture
def config(env):
    return Config.from_env(env)
