from __future__ import annotations

import os
from pathlib import Path

import pytest

from pm_football_bot.dotenv_store import delete_dotenv_keys, mask_secret, upsert_dotenv
from pm_football_bot.execution import (
    LiveTradingDisabled,
    live_client_options,
    lookup_proxy_wallet,
    resolve_funder,
    signature_type,
)


def test_mask_secret_hides_middle():
    assert mask_secret("0x1234567890abcdef") == "0x12…cdef"
    assert mask_secret("short") == "••••"


def test_upsert_and_delete_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / ".env"
    path.write_text("FOOTBALL_DATA_TOKEN=keep-me\nPK=old\n", encoding="utf-8")
    monkeypatch.delenv("PK", raising=False)
    upsert_dotenv({"PK": "new-key", "SIGNATURE_TYPE": "3"}, path=path)
    text = path.read_text(encoding="utf-8")
    assert "PK=new-key" in text
    assert "FOOTBALL_DATA_TOKEN=keep-me" in text
    assert os.environ["PK"] == "new-key"
    delete_dotenv_keys(["PK"], path=path)
    text = path.read_text(encoding="utf-8")
    assert "PK=" not in text
    assert "FOOTBALL_DATA_TOKEN=keep-me" in text
    assert "PK" not in os.environ


def test_signature_type_maps_eoa_to_deposit_wallet(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SIGNATURE_TYPE", raising=False)
    assert signature_type() == 3
    monkeypatch.setenv("SIGNATURE_TYPE", "0")
    assert signature_type() == 3
    monkeypatch.setenv("SIGNATURE_TYPE", "1")
    assert signature_type() == 1
    monkeypatch.setenv("SIGNATURE_TYPE", "2")
    assert signature_type() == 2


def test_resolve_funder_uses_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FUNDER", "0xabc")
    assert resolve_funder("0xdead") == "0xabc"


def test_resolve_funder_looks_up_proxy(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("FUNDER", raising=False)
    monkeypatch.delenv("POLYMARKET_WALLET_ADDRESS", raising=False)
    monkeypatch.setattr("pm_football_bot.execution.eoa_address", lambda pk: "0xEOA")
    monkeypatch.setattr(
        "pm_football_bot.execution.lookup_proxy_wallet",
        lambda address, gamma_host="https://gamma-api.polymarket.com": "0xDEPOSIT",
    )
    assert resolve_funder("0xpk") == "0xDEPOSIT"
    assert os.environ["FUNDER"] == "0xDEPOSIT"


def test_resolve_funder_missing_proxy(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("FUNDER", raising=False)
    monkeypatch.delenv("POLYMARKET_WALLET_ADDRESS", raising=False)
    monkeypatch.setattr("pm_football_bot.execution.eoa_address", lambda pk: "0xEOA")
    monkeypatch.setattr("pm_football_bot.execution.lookup_proxy_wallet", lambda *args, **kwargs: "")
    with pytest.raises(LiveTradingDisabled, match="deposit-wallet"):
        resolve_funder("0xpk")


def test_live_client_options_uses_deposit_wallet(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FUNDER", "0xDEPOSIT")
    monkeypatch.setenv("SIGNATURE_TYPE", "0")
    options = live_client_options("0xpk", "https://clob.polymarket.com")
    assert options["signature_type"] == 3
    assert options["funder"] == "0xDEPOSIT"
    assert options["key"] == "0xpk"


def test_lookup_proxy_wallet_handles_404(monkeypatch: pytest.MonkeyPatch):
    class _Resp:
        status_code = 404
        ok = False

        def json(self):
            return {}

    monkeypatch.setattr("pm_football_bot.execution.requests.get", lambda *args, **kwargs: _Resp())
    assert lookup_proxy_wallet("0xabc") == ""
