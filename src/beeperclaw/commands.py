"""Command handlers for beeperclaw."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from beeperclaw.bot import BeeperClawBot

from beeperclaw.opencode_client import OpenCodeAPIError, OpenCodeRateLimitError, PromptAttachment

logger = logging.getLogger(__name__)


def format_opencode_error(exc: OpenCodeAPIError) -> str:
    """Format OpenCode API errors for user-facing messages."""
    if isinstance(exc, OpenCodeRateLimitError):
        if exc.retry_after:
            return f"OpenCode rate limited. Retry after {exc.retry_after:.1f}s."
        return "OpenCode rate limited. Please retry shortly."
    if exc.status_code:
        return f"OpenCode API error (status {exc.status_code})."
    return f"OpenCode API error: {exc}"


@dataclass
class CommandResult:
    """Result of a command execution."""

    success: bool
    message: str
    data: dict[str, Any] | None = None


@dataclass(frozen=True)
class CommandContext:
    """Per-command Matrix context."""

    room_id: str
    sender: str
    event_id: str | None = None
    attachments: tuple[PromptAttachment, ...] = ()


class Command(ABC):
    """Base class for commands."""

    name: str
    description: str
    usage: str
    aliases: list[str] = []

    @abstractmethod
    async def execute(
        self, bot: BeeperClawBot, args: str, context: CommandContext
    ) -> CommandResult:
        """Execute the command.

        Args:
            bot: The bot instance
            args: Command arguments
            context: Matrix event context

        Returns:
            Command result
        """
        pass


def _format_connect_target(host: str, user: str | None) -> str:
    user = user.strip() if user else ""
    return f"{user}@{host}" if user else host


class BuildCommand(Command):
    """Execute a coding task with full access."""

    name = "build"
    description = "Execute a coding task with full access to modify files"
    usage = "/build <task description>"
    aliases = ["b", "do", "code"]

    async def execute(
        self, bot: BeeperClawBot, args: str, context: CommandContext
    ) -> CommandResult:
        if not args.strip():
            return CommandResult(
                success=False,
                message="Please provide a task description.\nUsage: /build <task>",
            )

        try:
            busy = await bot.get_inflight_status_for_room(context.room_id)
            if busy is not None:
                session_id, status = busy
                return CommandResult(
                    success=False,
                    message=(
                        f"Room already has a `{status}` task in session `{session_id[:8]}...`.\n"
                        "Wait for it to finish, use `/abort`, or use another room."
                    ),
                )

            session = await bot.get_or_create_session_for_room(context.room_id)

            await bot.opencode.send_message_async(
                session_id=session.id,
                content=args,
                agent="build",
                model=bot.current_model,
                attachments=list(context.attachments),
            )
            bot.register_pending_run(
                session_id=session.id,
                room_id=context.room_id,
                sender=context.sender,
                command_name=self.name,
                origin_event_id=context.event_id,
                state="running",
                attachments=context.attachments,
            )

            attachment_note = ""
            if context.attachments:
                attachment_note = f"Attachments: `{len(context.attachments)}`\n"

            return CommandResult(
                success=True,
                message=(
                    f"Task started with build agent.\nSession: `{session.id[:8]}...`\n\n"
                    f"{attachment_note}"
                    "I'll reply here when it's complete."
                ),
                data={"session_id": session.id},
            )
        except OpenCodeAPIError as e:
            logger.exception("Failed to execute build command")
            return CommandResult(
                success=False,
                message=f"Failed to start task: {format_opencode_error(e)}",
            )
        except Exception as e:
            logger.exception("Failed to execute build command")
            return CommandResult(
                success=False,
                message=f"Failed to start task: {e}",
            )


class PlanCommand(Command):
    """Analyze and plan without making changes."""

    name = "plan"
    description = "Analyze code and plan changes without modifying files"
    usage = "/plan <analysis request>"
    aliases = ["p", "analyze", "review"]

    async def execute(
        self, bot: BeeperClawBot, args: str, context: CommandContext
    ) -> CommandResult:
        if not args.strip():
            return CommandResult(
                success=False,
                message="Please provide an analysis request.\nUsage: /plan <request>",
            )

        try:
            busy = await bot.get_inflight_status_for_room(context.room_id)
            if busy is not None:
                session_id, status = busy
                return CommandResult(
                    success=False,
                    message=(
                        f"Room already has a `{status}` task in session `{session_id[:8]}...`.\n"
                        "Wait for it to finish, use `/abort`, or use another room."
                    ),
                )

            session = await bot.get_or_create_session_for_room(context.room_id)

            await bot.opencode.send_message_async(
                session_id=session.id,
                content=args,
                agent="plan",
                model=bot.current_model,
                attachments=list(context.attachments),
            )
            bot.register_pending_run(
                session_id=session.id,
                room_id=context.room_id,
                sender=context.sender,
                command_name=self.name,
                origin_event_id=context.event_id,
                state="running",
                attachments=context.attachments,
            )

            attachment_note = ""
            if context.attachments:
                attachment_note = f"Attachments: `{len(context.attachments)}`\n"

            return CommandResult(
                success=True,
                message=(
                    f"Analysis started with plan agent.\nSession: `{session.id[:8]}...`\n\n"
                    f"{attachment_note}"
                    "I'll reply here when it's complete."
                ),
                data={"session_id": session.id},
            )
        except OpenCodeAPIError as e:
            logger.exception("Failed to execute plan command")
            return CommandResult(
                success=False,
                message=f"Failed to start analysis: {format_opencode_error(e)}",
            )
        except Exception as e:
            logger.exception("Failed to execute plan command")
            return CommandResult(
                success=False,
                message=f"Failed to start analysis: {e}",
            )


class StatusCommand(Command):
    """Check current task status."""

    name = "status"
    description = "Check the status of current tasks"
    usage = "/status"
    aliases = ["s", "st"]

    async def execute(
        self, bot: BeeperClawBot, args: str, context: CommandContext
    ) -> CommandResult:
        try:
            session_id = args.strip() if args.strip() else bot.get_room_session_id(context.room_id)
            if not session_id:
                return CommandResult(
                    success=True,
                    message="No active session for this room.",
                )

            statuses = await bot.opencode.get_session_status()
            status = statuses.get(session_id)
            if status is None:
                return CommandResult(
                    success=True,
                    message=f"**Session Status:**\n[idle] `{session_id[:8]}...`",
                )

            line = f"[{status.status}] `{session_id[:8]}...`"
            if status.agent:
                line += f" ({status.agent})"

            return CommandResult(
                success=True,
                message=f"**Session Status:**\n{line}",
            )
        except OpenCodeAPIError as e:
            logger.exception("Failed to get status")
            return CommandResult(
                success=False,
                message=f"Failed to get status: {format_opencode_error(e)}",
            )
        except Exception as e:
            logger.exception("Failed to get status")
            return CommandResult(
                success=False,
                message=f"Failed to get status: {e}",
            )


class SessionsCommand(Command):
    """List all sessions."""

    name = "sessions"
    description = "List all OpenCode sessions"
    usage = "/sessions"
    aliases = ["ls", "list"]

    async def execute(
        self, bot: BeeperClawBot, args: str, context: CommandContext
    ) -> CommandResult:
        try:
            sessions = await bot.opencode.list_sessions()

            if not sessions:
                return CommandResult(
                    success=True,
                    message="No sessions found.",
                )

            lines = ["**Sessions:**\n"]
            for session in sessions[:10]:  # Limit to 10
                title = session.title or "Untitled"
                lines.append(f"• `{session.id[:8]}...` - {title}")

            if len(sessions) > 10:
                lines.append(f"\n... and {len(sessions) - 10} more")

            return CommandResult(
                success=True,
                message="\n".join(lines),
            )
        except OpenCodeAPIError as e:
            logger.exception("Failed to list sessions")
            return CommandResult(
                success=False,
                message=f"Failed to list sessions: {format_opencode_error(e)}",
            )
        except Exception as e:
            logger.exception("Failed to list sessions")
            return CommandResult(
                success=False,
                message=f"Failed to list sessions: {e}",
            )


class AbortCommand(Command):
    """Stop the current task."""

    name = "abort"
    description = "Stop the current running task"
    usage = "/abort [session_id]"
    aliases = ["stop", "cancel"]

    async def execute(
        self, bot: BeeperClawBot, args: str, context: CommandContext
    ) -> CommandResult:
        try:
            session_id = args.strip() if args.strip() else bot.get_room_session_id(context.room_id)

            if session_id is None:
                return CommandResult(
                    success=False,
                    message="No active session for this room.",
                )

            statuses = await bot.opencode.get_session_status()
            status = statuses.get(session_id)
            if status is None or status.status not in {"running", "waiting"}:
                return CommandResult(
                    success=False,
                    message=f"Session `{session_id[:8]}...` is not currently running.",
                )

            await bot.opencode.abort_session(session_id)
            bot.clear_pending_run(session_id)

            return CommandResult(
                success=True,
                message=f"Aborted session `{session_id[:8]}...`",
            )
        except OpenCodeAPIError as e:
            logger.exception("Failed to abort session")
            return CommandResult(
                success=False,
                message=f"Failed to abort: {format_opencode_error(e)}",
            )
        except Exception as e:
            logger.exception("Failed to abort session")
            return CommandResult(
                success=False,
                message=f"Failed to abort: {e}",
            )


class ModelCommand(Command):
    """Switch AI model."""

    name = "model"
    description = "Switch the AI model"
    usage = "/model <model_name>"
    aliases = ["m"]

    async def execute(
        self, bot: BeeperClawBot, args: str, context: CommandContext
    ) -> CommandResult:
        if not args.strip():
            # List available models
            return CommandResult(
                success=True,
                message=(
                    "**Available models:**\n"
                    "• `claude-opus-4.5` - Most capable\n"
                    "• `claude-sonnet-4.5` - Fast and capable\n"
                    "• `gemini-3-pro-high` - Google's best\n"
                    "• `gemini-3-flash` - Ultra fast\n\n"
                    "Usage: /model <name>"
                ),
            )

        model = args.strip()
        bot.set_current_model(model)

        return CommandResult(
            success=True,
            message=f"Switched to model: `{model}`",
        )


class SSHCommand(Command):
    """Show safe SSH and mosh connection strings."""

    name = "ssh"
    description = "Show configured SSH and mosh connection strings"
    usage = "/ssh"
    aliases = ["mosh"]

    async def execute(
        self, bot: BeeperClawBot, args: str, context: CommandContext
    ) -> CommandResult:
        del args, context

        host = (bot.config.bot.connect_host or "").strip()
        if not host:
            return CommandResult(
                success=True,
                message=(
                    "Remote access is not configured.\n\n"
                    "Set `bot.connect_host` to enable `/ssh` and `/mosh`.\n"
                    "You can also set `bot.connect_user` and `bot.connect_ssh_port`."
                ),
            )

        target = _format_connect_target(host, bot.config.bot.connect_user)
        ssh_port = bot.config.bot.connect_ssh_port

        ssh_command = f"ssh {target}"
        mosh_command = f"mosh {target}"
        if ssh_port != 22:
            ssh_command = f"ssh -p {ssh_port} {target}"
            mosh_command = f'mosh --ssh="ssh -p {ssh_port}" {target}'

        return CommandResult(
            success=True,
            message=(
                "**Remote access:**\n"
                f"SSH: `{ssh_command}`\n"
                f"Mosh: `{mosh_command}`\n\n"
                "This command only returns host, user, and port values from config."
            ),
        )


class HelpCommand(Command):
    """Show help information."""

    name = "help"
    description = "Show available commands"
    usage = "/help [command]"
    aliases = ["h", "?"]

    async def execute(
        self, bot: BeeperClawBot, args: str, context: CommandContext
    ) -> CommandResult:
        if args.strip():
            # Help for specific command
            cmd_name = args.strip().lower()
            cmd = bot.commands.get(cmd_name)
            if cmd is None:
                # Check aliases
                for c in bot.commands.values():
                    if cmd_name in c.aliases:
                        cmd = c
                        break

            if cmd is None:
                return CommandResult(
                    success=False,
                    message=f"Unknown command: {cmd_name}",
                )

            aliases = f" (aliases: {', '.join(cmd.aliases)})" if cmd.aliases else ""
            return CommandResult(
                success=True,
                message=f"**/{cmd.name}**{aliases}\n\n{cmd.description}\n\nUsage: `{cmd.usage}`",
            )

        # General help
        lines = ["**beeperclaw Commands:**\n"]
        unique_cmds = {cmd.name: cmd for cmd in bot.commands.values()}
        for name in sorted(unique_cmds):
            cmd = unique_cmds[name]
            lines.append(f"• `/{cmd.name}` - {cmd.description}")

        lines.append("\n\nUse `/help <command>` for more details.")

        return CommandResult(
            success=True,
            message="\n".join(lines),
        )


class AgentsCommand(Command):
    """List available agents."""

    name = "agents"
    description = "List available OpenCode agents"
    usage = "/agents"
    aliases = ["a"]

    async def execute(
        self, bot: BeeperClawBot, args: str, context: CommandContext
    ) -> CommandResult:
        try:
            agents = await bot.opencode.list_agents()

            if not agents:
                return CommandResult(
                    success=True,
                    message="No agents available.",
                )

            lines = ["**Available Agents:**\n"]
            for agent in agents:
                name = agent.get("name", "unknown")
                desc = agent.get("description", "No description")
                lines.append(f"• **{name}** - {desc}")

            return CommandResult(
                success=True,
                message="\n".join(lines),
            )
        except OpenCodeAPIError as e:
            logger.exception("Failed to list agents")
            return CommandResult(
                success=False,
                message=f"Failed to list agents: {format_opencode_error(e)}",
            )
        except Exception as e:
            logger.exception("Failed to list agents")
            return CommandResult(
                success=False,
                message=f"Failed to list agents: {e}",
            )


# Registry of all commands
ALL_COMMANDS: list[type[Command]] = [
    BuildCommand,
    PlanCommand,
    StatusCommand,
    SessionsCommand,
    AbortCommand,
    ModelCommand,
    SSHCommand,
    HelpCommand,
    AgentsCommand,
]
