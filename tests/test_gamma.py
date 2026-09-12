from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from pm_football_bot.config import League
from pm_football_bot.gamma import GammaClient


class _Resp:
    def __init__(self, payload) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _Session:
    def __init__(self, pages: dict[int, list]) -> None:
        self.headers: dict[str, str] = {}
        self.pages = pages
        self.offsets: list[int] = []

    def get(self, url, params=None, timeout=None):
        offset = int((params or {}).get("offset") or 0)
        self.offsets.append(offset)
        self.last_params = params or {}
        return _Resp(self.pages.get(offset, []))


def test_list_moneyline_events_stops_after_horizon():
    now = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    until = now + timedelta(hours=8)
    soon = {
        "title": "Chelsea FC vs. Hull City AFC",
        "slug": "epl-che-hul",
        "startTime": "2026-09-12T14:00:00Z",
    }
    pads = [
        {
            "title": f"Pad {i} exact score",
            "slug": f"epl-pad-{i}-exact-score",
            "startTime": "2026-09-12T14:00:00Z",
        }
        for i in range(49)
    ]
    session = _Session(
        {
            0: [soon, *pads],
            50: [
                {
                    "title": "Arsenal FC vs. Far Away FC",
                    "slug": "epl-ars-far",
                    "startTime": "2026-10-01T14:00:00Z",
                }
            ],
            100: [
                {
                    "title": "Should not fetch",
                    "slug": "epl-skip",
                    "startTime": "2026-11-01T14:00:00Z",
                }
            ],
        }
    )
    client = GammaClient(SimpleNamespace(gamma_host="https://gamma.example"), session=session)
    league = League("epl", "Premier League", "epl", "10188", 1, enabled=True)
    rows = client.list_moneyline_events(league, order="startTime", until=until)
    assert [row["slug"] for row in rows] == ["epl-che-hul"]
    assert session.offsets == [0, 50]
    assert session.last_params["order"] == "startTime"
    assert session.last_params["ascending"] == "true"
