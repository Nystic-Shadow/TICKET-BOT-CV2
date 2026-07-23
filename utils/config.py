"""Runtime configuration loaded exclusively from environment variables."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Local development reads the repository's .env file. Real process variables
# always win, so deployment-platform secret managers remain authoritative.
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _as_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Config:
    """Validated, immutable application settings.

    Secrets intentionally have no source-code fallback. Production deployments
    should inject these values through their process or secret manager.
    """

    TOKEN: str
    PREFIX: str
    BOT_STATUS: str
    BOT_STATUS_TYPE: str
    DATABASE_PATH: str
    TRIGGERS_DATABASE_PATH: str
    LOG_PATH: str
    LOG_LEVEL: str
    SUPPORT_SERVER: str
    STREAM_URL: str
    DEVELOPER_NAME: str
    DEFAULT_PANEL_FOOTER: str
    TICKET_BANNER_URL: str
    TICKET_RULES_TEXT: str
    EMOJI_DIRECTORY: str
    AUTO_SYNC_APPLICATION_EMOJIS: bool

    @classmethod
    def from_env(cls) -> "Config":
        support_server = os.getenv("SUPPORT_SERVER_URL", "").strip()
        return cls(
            TOKEN=os.getenv("DISCORD_TOKEN", "").strip(),
            PREFIX=os.getenv("BOT_PREFIX", "!"),
            BOT_STATUS=os.getenv("BOT_STATUS", "!help | /help"),
            BOT_STATUS_TYPE=os.getenv("BOT_STATUS_TYPE", "WATCHING").strip().upper(),
            DATABASE_PATH=os.getenv("DATABASE_PATH", "bot.db"),
            TRIGGERS_DATABASE_PATH=os.getenv("TRIGGERS_DATABASE_PATH", "triggers.db"),
            LOG_PATH=os.getenv("LOG_PATH", "bot.log"),
            LOG_LEVEL=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
            SUPPORT_SERVER=support_server,
            STREAM_URL=os.getenv("STREAM_URL", "").strip(),
            DEVELOPER_NAME="Nystic Shadow",
            DEFAULT_PANEL_FOOTER=os.getenv(
                "DEFAULT_PANEL_FOOTER",
                "Developed by Nystic Shadow",
            ),
            TICKET_BANNER_URL=os.getenv("TICKET_BANNER_URL", ""),
            TICKET_RULES_TEXT=os.getenv(
                "TICKET_RULES_TEXT",
                "Describe the issue clearly, include relevant evidence, and avoid duplicate tickets.",
            ).replace("\\n", "\n"),
            EMOJI_DIRECTORY=os.getenv("EMOJI_DIRECTORY", "emojis"),
            AUTO_SYNC_APPLICATION_EMOJIS=_as_bool(
                os.getenv("AUTO_SYNC_APPLICATION_EMOJIS"),
                default=True,
            ),
        )

    def setup_logging(self) -> None:
        level = getattr(logging, self.LOG_LEVEL, logging.INFO)
        log_path = Path(self.LOG_PATH)
        if log_path.parent != Path("."):
            log_path.parent.mkdir(parents=True, exist_ok=True)

        logging.basicConfig(
            level=level,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            handlers=[
                RotatingFileHandler(
                    log_path,
                    maxBytes=5 * 1024 * 1024,
                    backupCount=3,
                    encoding="utf-8",
                ),
                logging.StreamHandler(),
            ],
            force=True,
        )


config = Config.from_env()
