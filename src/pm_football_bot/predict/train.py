from __future__ import annotations

import argparse
import json

from pm_football_bot.predict.engine import clear_cache
from pm_football_bot.predict.features import build_features
from pm_football_bot.predict.history import clean_history, download_history, load_history
from pm_football_bot.predict.model import train_ensemble


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train the leakage-safe 1X2 ensemble.")
    parser.add_argument("--download", action="store_true", help="Re-download football-data.co.uk CSVs.")
    parser.add_argument("--min-rows", type=int, default=800)
    args = parser.parse_args(argv)

    print("Loading match history…")
    raw = download_history() if args.download else load_history(download=not _has_history())
    cleaned = clean_history(raw)
    print(f"  {len(cleaned)} finished matches")
    if cleaned.empty:
        print("No history downloaded. Check network access to football-data.co.uk.")
        return 1

    print("Building pre-match features (shifted rolling / pre-update ELO)…")
    featured = build_features(cleaned)
    print("Training ensemble (LR + RF + GB, chronological holdout)…")
    stats = train_ensemble(featured, min_train_rows=args.min_rows)
    clear_cache()
    print(json.dumps({k: v for k, v in stats.items() if k != "columns"}, indent=2))
    print(f"Saved {stats['path']}")
    return 0


def _has_history() -> bool:
    from pm_football_bot.predict.history import HISTORY_CSV

    return HISTORY_CSV.exists()


if __name__ == "__main__":
    raise SystemExit(main())
