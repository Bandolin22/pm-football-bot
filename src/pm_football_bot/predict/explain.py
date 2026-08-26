from __future__ import annotations

import os

from pm_football_bot.config import ROOT

_SYSTEM = (
    "You explain a football 1X2 probability stack. "
    "Use only the numbers in the user message. "
    "Do not mention injuries, lineups, transfers, or news that were not provided. "
    "Do not recommend placing a bet. "
    "Keep the answer under 120 words."
)


def explain_forecast(forecast) -> str:
    local = local_explanation(forecast)
    claude = _claude(forecast)
    return claude or local


def local_explanation(forecast) -> str:
    if forecast.error:
        return forecast.error
    blended = forecast.blended
    if blended is None or forecast.ml is None:
        return "No 1X2 probabilities to explain."
    lines = [
        f"{forecast.home_name} vs {forecast.away_name}: blended pick is {forecast.pick} "
        f"(H {blended.home:.0%} / D {blended.draw:.0%} / A {blended.away:.0%}).",
        f"ML only: H {forecast.ml[0]:.0%} / D {forecast.ml[1]:.0%} / A {forecast.ml[2]:.0%}.",
    ]
    if forecast.poly is not None:
        lines.append(
            f"Polymarket: H {forecast.poly[0]:.0%} / D {forecast.poly[1]:.0%} / A {forecast.poly[2]:.0%}."
        )
    if forecast.gaps:
        lines.append("Disagreement: " + "; ".join(forecast.gaps) + ".")
    lines.append(forecast.note)
    return " ".join(lines)


def _claude(forecast) -> str | None:
    key = _anthropic_key()
    if not key:
        return None
    try:
        import anthropic
    except ImportError:
        return None
    blended = forecast.blended
    if blended is None or forecast.ml is None:
        return None
    user = (
        f"Match: {forecast.home_name} vs {forecast.away_name}\n"
        f"Matched history names: {forecast.matched_home} / {forecast.matched_away}\n"
        f"ML Home/Draw/Away: {forecast.ml[0]:.3f} {forecast.ml[1]:.3f} {forecast.ml[2]:.3f}\n"
        f"Polymarket Home/Draw/Away: {forecast.poly}\n"
        f"Blend Home/Draw/Away: {blended.home:.3f} {blended.draw:.3f} {blended.away:.3f}\n"
        f"Weights: {blended.weights}\n"
        f"Gaps: {forecast.gaps or 'none'}\n"
        f"Pick: {forecast.pick}\n"
        "Explain disagreement if any. This is a second view on a harvest ticket, not a bet."
    )
    try:
        client = anthropic.Anthropic(api_key=key)
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=220,
            system=_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        parts = [block.text for block in message.content if getattr(block, "text", None)]
        text = "\n".join(parts).strip()
        return text or None
    except Exception:
        return None


def _anthropic_key() -> str | None:
    _load_dotenv()
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    return key or None


def _load_dotenv() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip().strip('"').strip("'")
        if name and name not in os.environ:
            os.environ[name] = value
