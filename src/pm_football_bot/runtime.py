"""Always-on watchlist alerter: HTTP health check plus Telegram polling loop."""

from __future__ import annotations

import json
import os
import threading
import time
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pm_football_bot.notify import POLL_SECONDS, run_once, send_telegram, telegram_creds

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))
_LOCK = threading.Lock()
_STATE: dict[str, str | None] = {
    "started": None,
    "last_ok": None,
    "last_error": None,
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _snapshot() -> dict[str, str | None]:
    with _LOCK:
        return dict(_STATE)


def _set(**fields: str | None) -> None:
    with _LOCK:
        _STATE.update(fields)


class HealthHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps({"ok": True, **_snapshot()}, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _announce() -> None:
    flag = (os.environ.get("ANNOUNCE_START") or "1").strip().lower()
    if flag in {"0", "false", "no"}:
        return
    creds = telegram_creds()
    if creds is None:
        return
    send_telegram(
        creds[0],
        creds[1],
        "Watchlist alerter is online.\n"
        "I will Telegram you ~1 hour before kickoff for Real Madrid, Barca, "
        "Atlético, Arsenal, Liverpool, City, United, Chelsea, Spurs, Inter, "
        "Milan, Juventus, Atalanta, Napoli, Como, Lazio, Bayern, Dortmund, and PSG, with live Polymarket 1X2.",
    )


def _watch_loop() -> None:
    wait = max(15, POLL_SECONDS)
    while True:
        try:
            code = run_once()
            if code == 2:
                _set(last_error="missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
                return
            _set(last_ok=_now(), last_error=None)
        except Exception as exc:
            _set(last_error=f"{exc}\n{traceback.format_exc()}")
            print(f"watch-alert check failed: {exc}")
        time.sleep(wait)


def main() -> int:
    if telegram_creds() is None:
        print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
        return 2
    _set(started=_now())
    try:
        _announce()
    except Exception as exc:
        print(f"startup Telegram ping failed: {exc}")
    worker = threading.Thread(target=_watch_loop, name="watch-alert", daemon=True)
    worker.start()
    print(f"watch-alert listening on {HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), HealthHandler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
