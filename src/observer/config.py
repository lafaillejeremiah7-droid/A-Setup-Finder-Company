from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no external dependency).

    Only sets keys that are not already present in the real environment, so
    an explicitly exported variable always wins over the file.
    """
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    chat_id: str


@dataclass(frozen=True)
class TradovateConfig:
    env: str  # "live" or "demo"
    username: str
    password: str
    app_id: str
    app_version: str
    cid: str
    sec: str

    @property
    def is_complete(self) -> bool:
        return all(
            [self.username, self.password, self.cid, self.sec, self.app_id, self.app_version]
        )

    @property
    def rest_base(self) -> str:
        return (
            "https://live.tradovateapi.com/v1"
            if self.env == "live"
            else "https://demo.tradovateapi.com/v1"
        )

    @property
    def md_url(self) -> str:
        # Market-data websocket is shared; auth differs by md access token.
        return "wss://md.tradovateapi.com/v1/websocket"


@dataclass(frozen=True)
class AppConfig:
    telegram: TelegramConfig
    tradovate: TradovateConfig
    symbol: str
    max_alerts_per_day: int


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"MISSING_ENV:{name}")
    return value


def load_config(dotenv_path: str | os.PathLike[str] | None = None) -> AppConfig:
    """Load configuration from environment (optionally seeded from a .env file)."""
    root = Path(__file__).resolve().parents[2]
    _load_dotenv(Path(dotenv_path) if dotenv_path else root / ".env")

    telegram = TelegramConfig(
        bot_token=_require("TELEGRAM_BOT_TOKEN"),
        chat_id=_require("TELEGRAM_CHAT_ID"),
    )
    tradovate = TradovateConfig(
        env=os.environ.get("TRADOVATE_ENV", "live").strip() or "live",
        username=os.environ.get("TRADOVATE_USERNAME", "").strip(),
        password=os.environ.get("TRADOVATE_PASSWORD", "").strip(),
        app_id=os.environ.get("TRADOVATE_APP_ID", "MGCV26Observer").strip(),
        app_version=os.environ.get("TRADOVATE_APP_VERSION", "1.0").strip(),
        cid=os.environ.get("TRADOVATE_CID", "").strip(),
        sec=os.environ.get("TRADOVATE_SEC", "").strip(),
    )
    symbol = os.environ.get("SYMBOL", "MNQU6").strip() or "MNQU6"
    try:
        max_alerts = int(os.environ.get("MAX_ALERTS_PER_DAY", "2"))
    except ValueError as exc:
        raise ConfigError("INVALID_MAX_ALERTS_PER_DAY") from exc
    if max_alerts < 1:
        raise ConfigError("MAX_ALERTS_PER_DAY_MUST_BE_POSITIVE")

    return AppConfig(
        telegram=telegram,
        tradovate=tradovate,
        symbol=symbol,
        max_alerts_per_day=max_alerts,
    )
