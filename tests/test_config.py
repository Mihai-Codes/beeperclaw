"""Tests for configuration defaults."""

from beeperclaw.config import Config


def test_bot_connect_settings_default_to_safe_values() -> None:
    """Remote access helper config should be disabled by default."""
    config = Config.model_validate({"matrix": {"username": "@beeperclaw-bot:matrix.org"}})

    assert config.bot.connect_host is None
    assert config.bot.connect_user is None
    assert config.bot.connect_ssh_port == 22
