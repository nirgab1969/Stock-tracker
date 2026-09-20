"""
Orchestrates one full analysis pass for a single watchlist symbol:
fetch data -> run each pillar -> combine into the final result dict that
gets cached in the DB and served to the dashboard / used to decide alerts.
"""
import logging

from .. import market_data
from . import fundamentals as fund_mod
from . import indicators as ind
from . import scoring as sc
from . import sentiment as sent_mod

logger = logging.getLogger(__name__)


def analyze_symbol(watchlist_row: dict) -> dict:
    symbol = watchlist_row["symbol"]
    position = {
        "in_position": bool(watchlist_row.get("in_position")),
        "entry_price": watchlist_row.get("entry_price"),
    }

    result = {
        "symbol": symbol,
        "display_name": watchlist_row.get("display_name") or symbol,
        "market": watchlist_row.get("market", "US"),
        "position": position,
        "error": None,
    }

    try:
        history = market_data.get_history(symbol)
    except market_data.MarketDataError as e:
        logger.warning("skipping %s this scan: %s", symbol, e)
        result["error"] = str(e)
        return result

    if history is None or history.empty or len(history) < 30:
        result["error"] = "not enough price history returned for analysis"
        return result

    tech = ind.compute_all(history)
    t_score, t_reasons = sc.technical_score(tech)

    market_data.sleep_between_requests()
    fundamentals_raw = market_data.get_fundamentals(symbol)
    fund_result = fund_mod.analyze_fundamentals(fundamentals_raw)

    market_data.sleep_between_requests()
    news = market_data.get_news(symbol)
    sent_result = sent_mod.analyze_sentiment(news)

    composite, weights_used = sc.composite_score(t_score, fund_result["score"], sent_result["score"])
    signals = sc.generate_signals(tech, position)

    result.update({
        "technical": tech,
        "technical_score": round(t_score, 1),
        "technical_reasons": t_reasons,
        "fundamentals_raw": fundamentals_raw,
        "fundamental_score": fund_result["score"],
        "fundamental_details": fund_result["details"],
        "sentiment_score": sent_result["score"],
        "sentiment_headlines": sent_result["headlines"],
        "composite_score": composite,
        "weights_used": weights_used,
        "potential_label": sc.potential_label(composite),
        "signals": signals,
    })
    return result
