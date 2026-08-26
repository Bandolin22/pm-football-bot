from __future__ import annotations

import os
from typing import Any

from pm_football_bot.config import Settings
from pm_football_bot.models import Ticket


class LiveTradingDisabled(RuntimeError):
    pass


def place_ticket(ticket: Ticket, settings: Settings) -> dict[str, Any]:
    """Post a GTC maker buy. Dry-run unless settings.dry_run is false and keys exist."""
    if settings.dry_run:
        return {"status": "dry_run", "ticket": ticket}

    pk = os.environ.get("PK")
    if not pk:
        raise LiveTradingDisabled("Set PK in .env and dry_run: false before --live")

    try:
        from py_clob_client_v2 import (
            ApiCreds,
            ClobClient,
            OrderArgs,
            OrderType,
            PartialCreateOrderOptions,
            Side,
        )
    except ImportError as exc:
        raise LiveTradingDisabled(
            "Install live extras: pip install -e .[live]"
        ) from exc

    host = settings.clob_host
    chain_id = 137
    creds = None
    if os.environ.get("CLOB_API_KEY"):
        creds = ApiCreds(
            api_key=os.environ["CLOB_API_KEY"],
            api_secret=os.environ["CLOB_SECRET"],
            api_passphrase=os.environ["CLOB_PASS_PHRASE"],
        )
        client = ClobClient(host=host, chain_id=chain_id, key=pk, creds=creds)
    else:
        client = ClobClient(host=host, chain_id=chain_id, key=pk)
        creds = client.create_or_derive_api_key()
        client = ClobClient(host=host, chain_id=chain_id, key=pk, creds=creds)

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
