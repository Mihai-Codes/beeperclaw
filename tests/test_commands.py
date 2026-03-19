"""Tests for command handlers."""

from types import SimpleNamespace

import pytest

from codebeep.commands import HelpCommand, SSHCommand
from codebeep.config import Config


def make_config(**bot_overrides: object) -> Config:
    """Build a minimal config for command tests."""
    payload = {
        "matrix": {"username": "@codebeep-bot:matrix.org"},
        "bot": bot_overrides,
    }
    return Config.model_validate(payload)


@pytest.mark.asyncio
async def test_ssh_command_requires_configured_host() -> None:
    """The connect helper should stay disabled until a host is configured."""
    command = SSHCommand()
    bot = SimpleNamespace(config=make_config())

    result = await command.execute(bot, "")

    assert result.success is True
    assert "not configured" in result.message.lower()
    assert "`bot.connect_host`" in result.message


@pytest.mark.asyncio
async def test_ssh_command_formats_default_commands() -> None:
    """The connect helper should return both SSH and mosh strings."""
    command = SSHCommand()
    bot = SimpleNamespace(
        config=make_config(connect_host="codebeep.tailnet.example", connect_user="mihai")
    )

    result = await command.execute(bot, "")

    assert result.success is True
    assert "`ssh mihai@codebeep.tailnet.example`" in result.message
    assert "`mosh mihai@codebeep.tailnet.example`" in result.message


@pytest.mark.asyncio
async def test_ssh_command_formats_non_default_port() -> None:
    """The connect helper should include custom SSH port handling."""
    command = SSHCommand()
    bot = SimpleNamespace(
        config=make_config(
            connect_host="100.64.0.42",
            connect_user="mihai",
            connect_ssh_port=2222,
        )
    )

    result = await command.execute(bot, "")

    assert "`ssh -p 2222 mihai@100.64.0.42`" in result.message
    assert '`mosh --ssh="ssh -p 2222" mihai@100.64.0.42`' in result.message


@pytest.mark.asyncio
async def test_help_lists_ssh_command_once() -> None:
    """General help should include the command once despite aliases."""
    ssh = SSHCommand()
    help_command = HelpCommand()
    bot = SimpleNamespace(
        config=make_config(),
        commands={"ssh": ssh, "mosh": ssh},
    )

    result = await help_command.execute(bot, "")

    assert result.success is True
    assert result.message.count("`/ssh`") == 1

