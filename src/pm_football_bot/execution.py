from __future__ import annotations

import os
from typing import Any

import requests

from pm_football_bot.config import Settings
from pm_football_bot.dotenv_store import upsert_dotenv
from pm_football_bot.models import Ticket


class LiveTradingDisabled(RuntimeError):
    pass


_CLIENT: Any = None
_CLIENT_KEY: tuple[str, str, int, str] | None = None
_DEPOSIT_HINT = (
    "Polymarket now requires the deposit-wallet flow. "
    "Set FUNDER to the wallet on your polymarket.com profile (not the PK address)."
)


def reset_live_client() -> None:
    global _CLIENT, _CLIENT_KEY
    _CLIENT = None
    _CLIENT_KEY = None


def env_pk() -> str:
    return (os.environ.get("PK") or "").strip()


def env_funder() -> str:
    return (os.environ.get("FUNDER") or os.environ.get("POLYMARKET_WALLET_ADDRESS") or "").strip()


def signature_type() -> int:
    """EOA makers (type 0) are rejected; default to deposit wallet (type 3)."""
    raw = (os.environ.get("SIGNATURE_TYPE") or "3").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 3
    if value == 0:
        return 3
    return value


def eoa_address(pk: str) -> str:
    key = pk.strip()
    if not key.startswith("0x"):
        key = "0x" + key
    try:
        from eth_account import Account

        return Account.from_key(key).address
    except Exception:
        from py_clob_client_v2.signer import Signer

        return Signer(key, 137).address()


def lookup_proxy_wallet(address: str, gamma_host: str = "https://gamma-api.polymarket.com") -> str:
    url = f"{gamma_host.rstrip('/')}/public-profile"
    try:
        response = requests.get(url, params={"address": address}, timeout=20)
    except requests.RequestException:
        return ""
    if response.status_code in {400, 404}:
        return ""
    if not response.ok:
        return ""
    try:
        payload = response.json()
    except ValueError:
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("proxyWallet") or "").strip()


def resolve_funder(pk: str, *, persist: bool = False) -> str:
    existing = env_funder()
    if existing:
        return existing
    try:
        eoa = eoa_address(pk)
    except Exception as exc:
        raise LiveTradingDisabled(_DEPOSIT_HINT) from exc
    proxy = lookup_proxy_wallet(eoa)
    if proxy and proxy.lower() != eoa.lower():
        if persist:
            upsert_dotenv({"FUNDER": proxy})
        else:
            os.environ["FUNDER"] = proxy
        return proxy
    raise LiveTradingDisabled(_DEPOSIT_HINT)


def live_client_options(pk: str, host: str) -> dict[str, Any]:
    funder = resolve_funder(pk, persist=True)
    return {
        "host": host,
        "chain_id": 137,
        "key": pk,
        "signature_type": signature_type(),
        "funder": funder,
    }


def _api_creds(api_creds_cls: Any) -> Any | None:
    if not os.environ.get("CLOB_API_KEY"):
        return None
    return api_creds_cls(
        api_key=os.environ["CLOB_API_KEY"],
        api_secret=os.environ.get("CLOB_SECRET") or "",
        api_passphrase=os.environ.get("CLOB_PASS_PHRASE") or "",
    )


def live_client(settings: Settings) -> Any:
    """Authenticated CLOB client using the deposit-wallet (POLY_1271) maker."""
    global _CLIENT, _CLIENT_KEY
    pk = env_pk()
    if not pk:
        raise LiveTradingDisabled(
            "Set PK in the Watchlist keeper field, .env, or Streamlit secrets before live orders"
        )
    options = live_client_options(pk, settings.clob_host)
    cache_key = (pk, str(options["funder"]), int(options["signature_type"]), settings.clob_host)
    if _CLIENT is not None and _CLIENT_KEY == cache_key:
        return _CLIENT
    try:
        from py_clob_client_v2 import ApiCreds, ClobClient
    except ImportError as exc:
        raise LiveTradingDisabled("Install live extras: pip install -e .[live]") from exc

    creds = _api_creds(ApiCreds)
    if creds is None:
        bootstrap = ClobClient(**options)
        creds = bootstrap.create_or_derive_api_key()
    client = ClobClient(**options, creds=creds)
    _CLIENT = client
    _CLIENT_KEY = cache_key
    return client


def place_ticket(ticket: Ticket, settings: Settings) -> dict[str, Any]:
    """Post a GTC maker buy. Dry-run unless settings.dry_run is false and keys exist."""
    if settings.dry_run:
        return {"status": "dry_run", "ticket": ticket}

    try:
        from py_clob_client_v2 import OrderArgs, OrderType, PartialCreateOrderOptions, Side
    except ImportError as exc:
        raise LiveTradingDisabled("Install live extras: pip install -e .[live]") from exc

    client = live_client(settings)
    try:
        return client.create_and_post_order(
            order_args=OrderArgs(
                token_id=ticket.token_id,
                price=ticket.price,
                side=Side.BUY,
                size=ticket.shares,
            ),
            options=PartialCreateOrderOptions(tick_size="0.01"),
            order_type=OrderType.GTC,
        )
    except Exception as exc:
        text = str(exc).lower()
        if "deposit wallet" in text or "maker address not allowed" in text:
            raise RuntimeError(f"{exc}. {_DEPOSIT_HINT}") from exc
        raise
