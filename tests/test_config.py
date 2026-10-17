import pytest

from voice_notify.config import Config, ConfigError


def test_negative_chat_and_multiple_channels(config):
    assert config.telegram_chat_id == -1001234567890
    assert config.channel_ids == frozenset({200, 300})


@pytest.mark.parametrize(
    "key",
    [
        "DISCORD_BOT_TOKEN",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "DISCORD_GUILD_ID",
        "DISCORD_VOICE_CHANNEL_IDS",
    ],
)
@pytest.mark.parametrize("missing", [True, False])
def test_required(env, key, missing):
    if missing:
        del env[key]
    else:
        env[key] = "  "
    with pytest.raises(ConfigError, match=key):
        Config.from_env(env)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("DISCORD_BOT_TOKEN", "Bot secret token"),
        ("DISCORD_BOT_TOKEN", "token\nheader"),
        ("TELEGRAM_BOT_TOKEN", "123:token/path"),
        ("TELEGRAM_BOT_TOKEN", "https://evil.example"),
        ("TELEGRAM_CHAT_ID", "0"),
        ("TELEGRAM_CHAT_ID", "@username"),
        ("TELEGRAM_CHAT_ID", "-9223372036854775808"),
        ("DISCORD_GUILD_ID", "-100"),
        ("DISCORD_GUILD_ID", "18446744073709551616"),
        ("DISCORD_VOICE_CHANNEL_IDS", "200,,300"),
        ("DISCORD_VOICE_CHANNEL_IDS", "200,"),
        ("DISCORD_VOICE_CHANNEL_IDS", "0"),
        ("DISCORD_VOICE_CHANNEL_IDS", "200,text"),
        ("DISCORD_VOICE_CHANNEL_IDS", "-200"),
    ],
)
def test_invalid_values_are_not_echoed(env, key, value):
    env[key] = value
    with pytest.raises(ConfigError) as caught:
        Config.from_env(env)
    assert key in str(caught.value)
    assert env["DISCORD_BOT_TOKEN"] not in str(caught.value)
    assert env["TELEGRAM_BOT_TOKEN"] not in str(caught.value)


def test_whitespace_deduplication_and_positive_chat(env):
    env["DISCORD_VOICE_CHANNEL_IDS"] = " 200 , 300,200 "
    env["TELEGRAM_CHAT_ID"] = "123"
    config = Config.from_env(env)
    assert config.channel_ids == frozenset({200, 300})
    assert config.telegram_chat_id == 123


def test_repr_hides_tokens(config):
    assert config.discord_token not in repr(config)
    assert config.telegram_token not in repr(config)
