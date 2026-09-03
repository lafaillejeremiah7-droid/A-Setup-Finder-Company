from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from src.observer.config import TelegramConfig


class NotifyError(RuntimeError):
    """Raised when a Telegram message cannot be delivered."""


@dataclass(frozen=True)
class TelegramNotifier:
    config: TelegramConfig
    timeout_seconds: float = 10.0

    def _url(self) -> str:
        return f"https://api.telegram.org/bot{self.config.bot_token}/sendMessage"

    def send(self, text: str) -> None:
        """Send a Markdown message to the configured chat.

        Uses only the standard library so the observer has zero runtime
        dependencies. Raises NotifyError on transport or API failure.
        """
        payload = json.dumps(
            {
                "chat_id": self.config.chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }
        ).encode("utf-8")

        request = urllib.request.Request(
            self._url(),
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise NotifyError(f"TELEGRAM_HTTP_{exc.code}:{detail}") from exc
        except urllib.error.URLError as exc:
            raise NotifyError(f"TELEGRAM_TRANSPORT_ERROR:{exc.reason}") from exc

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise NotifyError("TELEGRAM_BAD_RESPONSE") from exc
        if not parsed.get("ok", False):
            raise NotifyError(f"TELEGRAM_API_ERROR:{parsed.get('description', 'unknown')}")
