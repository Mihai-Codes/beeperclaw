"""Command-line interface for beeperclaw."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.logging import RichHandler

from beeperclaw import __version__
from beeperclaw.config import Config

console = Console()


def setup_logging(level: str, log_file: str | None = None) -> None:
    """Set up logging configuration.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        log_file: Optional log file path
    """
    handlers: list[logging.Handler] = [
        RichHandler(
            console=console,
            show_time=True,
            show_path=False,
            rich_tracebacks=True,
        )
    ]

    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        handlers.append(file_handler)

    logging.basicConfig(
        level=getattr(logging, level.upper()),
        handlers=handlers,
    )


@click.group()
@click.version_option(version=__version__)
def main() -> None:
    """beeperclaw - AI coding agent accessible from anywhere via Beeper/Matrix."""
    pass


@main.command()
@click.option(
    "--config",
    "-c",
    type=click.Path(exists=True, path_type=Path),
    help="Path to config file",
)
@click.option(
    "--daemon",
    "-d",
    is_flag=True,
    help="Run in daemon mode (background)",
)
def run(config: Path | None, daemon: bool) -> None:
    """Start the beeperclaw bot."""
    try:
        cfg = Config.load(config)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("\nCreate a config.yaml file or use --config to specify one.")
        console.print("See config.example.yaml for reference.")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error loading config:[/red] {e}")
        sys.exit(1)

    setup_logging(cfg.logging.level, cfg.logging.file)

    if daemon:
        console.print("[yellow]Daemon mode not yet implemented.[/yellow]")
        console.print("Running in foreground instead...")

    console.print(f"[green]Starting beeperclaw v{__version__}[/green]")
    console.print(f"Connecting to: {cfg.matrix.homeserver}")
    console.print(f"OpenCode server: {cfg.opencode.server_url}")

    from beeperclaw.bot import run_bot

    try:
        asyncio.run(run_bot(cfg))
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@main.command()
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=Path("config.yaml"),
    help="Output path for config file",
)
def init(output: Path) -> None:
    """Initialize a new configuration file."""
    if output.exists():
        if not click.confirm(f"{output} already exists. Overwrite?"):
            return

    # Copy example config
    example_path = Path(__file__).parent.parent.parent.parent / "config.example.yaml"
    if example_path.exists():
        import shutil

        shutil.copy(example_path, output)
        console.print(f"[green]Created config file:[/green] {output}")
    else:
        # Create minimal config
        minimal_config = """# beeperclaw Configuration
matrix:
  homeserver: "https://matrix.beeper.com"
  username: "@your-bot:beeper.local"
  password: "your-password"
  allowed_users:
    - "@your-account:beeper.local"

opencode:
  server_url: "http://127.0.0.1:4096"
  default_agent: "build"

providers:
  primary: "antigravity"
  antigravity:
    base_url: "http://127.0.0.1:8045/v1"
    api_key: "sk-antigravity"
"""
        output.write_text(minimal_config)
        console.print(f"[green]Created config file:[/green] {output}")

    console.print("\nEdit the config file with your credentials, then run:")
    console.print("  beeperclaw run")


@main.command()
@click.option(
    "--config",
    "-c",
    type=click.Path(exists=True, path_type=Path),
    help="Path to config file",
)
def check(config: Path | None) -> None:
    """Check configuration and connectivity."""
    try:
        cfg = Config.load(config)
        console.print("[green]Config loaded successfully[/green]")
    except Exception as e:
        console.print(f"[red]Config error:[/red] {e}")
        sys.exit(1)

    # Check OpenCode connection
    console.print(f"\nChecking OpenCode server at {cfg.opencode.server_url}...")

    async def check_opencode() -> None:
        from beeperclaw.opencode_client import OpenCodeClient

        client = OpenCodeClient(cfg.opencode.server_url)
        try:
            health = await client.health_check()
            console.print(
                f"[green]OpenCode connected[/green] - version {health.get('version', 'unknown')}"
            )

            # List agents
            agents = await client.list_agents()
            console.print(f"  Available agents: {', '.join(a.get('name', '?') for a in agents)}")
        except Exception as e:
            console.print(f"[red]OpenCode connection failed:[/red] {e}")
        finally:
            await client.close()

    asyncio.run(check_opencode())

    # Check Antigravity if configured
    if cfg.providers.primary == "antigravity":
        console.print(f"\nChecking Antigravity Manager at {cfg.providers.antigravity.base_url}...")
        import httpx

        try:
            response = httpx.get(
                f"{cfg.providers.antigravity.base_url}/models",
                headers={"Authorization": f"Bearer {cfg.providers.antigravity.api_key}"},
                timeout=5.0,
            )
            if response.status_code == 200:
                console.print("[green]Antigravity Manager connected[/green]")
            else:
                console.print(
                    f"[yellow]Antigravity returned status {response.status_code}[/yellow]"
                )
        except Exception as e:
            console.print(f"[red]Antigravity connection failed:[/red] {e}")

    console.print("\n[green]All checks complete![/green]")


@main.command()
def version() -> None:
    """Show version information."""
    console.print(f"beeperclaw v{__version__}")


if __name__ == "__main__":
    main()
