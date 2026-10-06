"""
Broad-market discovery scan: screens a whole index universe (not just the
user's own watchlist) for high-potential stocks.

Two stages, to keep this fast and easy on Yahoo Finance's unofficial API:
  1. Technical-only pre-filter across the ENTIRE universe (cheap: one batched
     price-history fetch), ranked by technical_score - keep only the top N.
  2. Full technical + fundamental + sentiment analysis (same engine used for
     the watchlist) on just those top N candidates.

Anything that clears the high-potential threshold in stage 2, and isn't
already on the user's watchlist, gets written to the `discoveries` table and
raises a DISCOVERY alert (push + log) - completely separate from the user's
own tracked positions.
"""
import gc
import logging

from .. import config, db, market_data
from . import indicators as ind
from . import scoring as sc
from .engine import analyze_symbol

logger = logging.getLogger(__name__)


def _stage1_technical_prefilter(universe: list) -> list:
    """
    universe: [{symbol, name, market}]. Returns the same dicts, sorted best-first, top N only.

    Processes the universe chunk-by-chunk via market_data.iter_history_batches() and scores
    each chunk immediately, so at most one chunk's worth of price history is ever resident
    in memory - NOT the whole universe (~600+ tickers) at once. That "fetch everything, then
    score everything" pattern was the actual cause of repeated OOM restarts on Render.
    """
    by_symbol = {u["symbol"]: u for u in universe}
    symbols = list(by_symbol.keys())

    scored = []
    for chunk in market_data.iter_history_batches(symbols):
        for sym, df in chunk.items():
            if df is None or df.empty or len(df) < 30:
                continue
            try:
                tech = ind.compute_all(df)
                t_score, _ = sc.technical_score(tech)
            except Exception as e:
                logger.debug("stage1 skip %s: %s", sym, e)
                continue
            scored.append({**by_symbol[sym], "_stage1_score": t_score})
        del chunk
        gc.collect()

    scored.sort(key=lambda x: x["_stage1_score"], reverse=True)
    return scored[: config.DISCOVERY_STAGE1_TOP_N]


def run_discovery_scan() -> dict:
    if not config.DISCOVERY_ENABLED:
        return {"skipped": "discovery disabled"}

    from .. import universe as universe_mod  # local import avoids a circular import at module load time

    universe = universe_mod.get_universe(config.DISCOVERY_UNIVERSE)
    watchlist_symbols = {row["symbol"].upper() for row in db.list_watchlist()}
    universe = [u for u in universe if u["symbol"].upper() not in watchlist_symbols]

    logger.info("discovery scan: %d candidates in universe (after excluding watchlist)", len(universe))
    if not universe:
        return {"universe_size": 0, "candidates_analyzed": 0, "discovered": []}

    shortlist = _stage1_technical_prefilter(universe)
    logger.info("discovery scan: stage 1 kept top %d candidates", len(shortlist))

    discovered = []
    for candidate in shortlist:
        synthetic_row = {
            "symbol": candidate["symbol"],
            "display_name": candidate["name"],
            "market": candidate["market"],
            "in_position": False,
            "entry_price": None,
        }
        try:
            result = analyze_symbol(synthetic_row)
        except Exception:
            logger.exception("discovery: unexpected error analyzing %s", candidate["symbol"])
            continue

        market_data.sleep_between_requests()

        if result.get("error"):
            continue

        composite = result.get("composite_score")
        if composite is not None and composite >= config.HIGH_POTENTIAL_SCORE_THRESHOLD:
            db.upsert_discovery(result["symbol"], result["display_name"], result["market"], composite, result)
            discovered.append({"symbol": result["symbol"], "composite_score": composite})

    return {
        "universe_size": len(universe),
        "candidates_analyzed": len(shortlist),
        "discovered": discovered,
    }
