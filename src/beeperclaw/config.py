"""Configuration management for beeperclaw."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class MatrixConfig(BaseModel):
    """Matrix/Beeper connection configuration."""

    homeserver: str = "https://matrix.beeper.com"
    username: str
    password: str | None = None
    access_token: str | None = None
    allowed_users: list[str] = Field(default_factory=list)
    device_name: str = "beeperclaw"


class OpenCodeAuthConfig(BaseModel):
    """OpenCode basic authentication configuration."""

    username: str | None = None
    password: str | None = None


class OpenCodeConfig(BaseModel):
    """OpenCode server configuration."""

    server_url: str = "http://127.0.0.1:4096"
    auth: OpenCodeAuthConfig | None = None
    default_agent: str = "build"
    project_path: str | None = None
    session_timeout: int = 3600


class AntigravityConfig(BaseModel):
    """Antigravity Manager configuration."""

    base_url: str = "http://127.0.0.1:8045/v1"
    api_key: str = "sk-antigravity"
    default_model: str = "claude-opus-4-5-thinking"


class CopilotConfig(BaseModel):
    """GitHub Copilot configuration."""

    default_model: str = "claude-opus-4.5"


class GoogleConfig(BaseModel):
    """Google AI configuration."""

    api_key: str = ""
    default_model: str = "gemini-3-pro-high"


class ProvidersConfig(BaseModel):
    """AI providers configuration."""

    primary: str = "antigravity"
    fallback: list[str] = Field(default_factory=lambda: ["copilot", "google"])
    antigravity: AntigravityConfig = Field(default_factory=AntigravityConfig)
    copilot: CopilotConfig = Field(default_factory=CopilotConfig)
    google: GoogleConfig = Field(default_factory=GoogleConfig)


class GitHubConfig(BaseModel):
    """GitHub integration configuration."""

    token: str = ""
    default_repo: str | None = None
    auto_assign: bool = True


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: str = "INFO"
    file: str | None = None
    console: bool = True


class BotConfig(BaseModel):
    """Bot behavior configuration."""

    prefix: str = "/"
    typing_indicator: bool = True
    max_message_length: int = 4000
    rate_limit: int = 30
    unknown_command_reply: bool = True
    state_path: str = ".beeperclaw_store/state.json"
    dedup_enabled: bool = True
    dedup_cache_size: int = 500
    dedup_window_seconds: int = 10
    connect_host: str | None = None
    connect_user: str | None = None
    connect_ssh_port: int = 22


class Config(BaseModel):
    """Main configuration model."""

    matrix: MatrixConfig
    opencode: OpenCodeConfig = Field(default_factory=OpenCodeConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    bot: BotConfig = Field(default_factory=BotConfig)

    @classmethod
    def load(cls, path: str | Path | None = None) -> Config:
        """Load configuration from a YAML file.

        Args:
            path: Path to the config file. If None, searches for config.yaml
                  in the current directory and ~/.config/beeperclaw/

        Returns:
            Loaded configuration

        Raises:
            FileNotFoundError: If no config file is found
            ValueError: If the config file is invalid
        """
        if path is None:
            # Search for config file
            search_paths = [
                Path("config.yaml"),
                Path("config.yml"),
                Path.home() / ".config" / "beeperclaw" / "config.yaml",
                Path.home() / ".config" / "beeperclaw" / "config.yml",
            ]
            for search_path in search_paths:
                if search_path.exists():
                    path = search_path
                    break
            else:
                raise FileNotFoundError(
                    "No config file found. Create config.yaml or specify path with --config"
                )

        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(path) as f:
            data = yaml.safe_load(f)

        # Expand environment variables in string values
        data = cls._expand_env_vars(data)

        return cls.model_validate(data)

    @classmethod
    def _expand_env_vars(cls, data: Any) -> Any:
        """Recursively expand environment variables in config values."""
        if isinstance(data, dict):
            return {k: cls._expand_env_vars(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [cls._expand_env_vars(item) for item in data]
        elif isinstance(data, str):
            return os.path.expandvars(data)
        return data

    def save(self, path: str | Path) -> None:
        """Save configuration to a YAML file.

        Args:
            path: Path to save the config file
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w") as f:
            yaml.dump(self.model_dump(), f, default_flow_style=False, sort_keys=False)
