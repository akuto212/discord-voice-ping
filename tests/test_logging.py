import json
import logging

from voice_notify.logging_setup import SafeJsonFormatter


def test_secrets_and_newlines_are_safe():
    formatter = SafeJsonFormatter(("discord.secret", "123:telegram_secret"))
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        "",
        0,
        "token=%s url=%s\ninjected",
        ("discord.secret", "https://api.telegram.org/bot123:telegram_secret/sendMessage"),
        None,
    )
    output = formatter.format(record)
    assert "discord.secret" not in output
    assert "123:telegram_secret" not in output
    assert "[REDACTED]" in output
    assert len(output.splitlines()) == 1
    assert json.loads(output)["level"] == "ERROR"


def test_tracebacks_are_also_redacted():
    import sys

    try:
        raise ValueError("discord.secret")
    except ValueError:
        record = logging.LogRecord("test", logging.ERROR, "", 0, "failure", (), sys.exc_info())
    output = SafeJsonFormatter(("discord.secret",)).format(record)
    assert "discord.secret" not in output
    assert "Traceback" in output
