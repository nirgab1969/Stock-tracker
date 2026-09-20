"""
Ticker "universe" for the discovery scanner - the broad list of candidate
symbols to screen for high-potential stocks, separate from the user's own
curated watchlist.

S&P 500: fetched live from Wikipedia (actively maintained, structured table)
and cached in the DB for a week at a time, with a small hardcoded fallback
list if the fetch ever fails.

TA-125: no reliably-updated free structured source was found, so it's a
static snapshot shipped in app/data/ta125_fallback.json - edit that file
directly if you want to refresh/adjust it.
"""
import io
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from . import config, db

logger = logging.getLogger(__name__)

SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
UNIVERSE_CACHE_TTL_DAYS = 7

DATA_DIR = Path(__file__).resolve().parent / "data"

# Small hardcoded fallback (large, liquid, well-known names) used only if the
# live Wikipedia fetch fails AND there's no usable cache yet.
SP500_FALLBACK = [
    "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "GOOG", "META", "BRK-B", "TSLA", "AVGO",
    "JPM", "LLY", "V", "XOM", "UNH", "MA", "COST", "HD", "PG", "JNJ",
    "NFLX", "BAC", "ABBV", "CRM", "WMT", "MRK", "AMD", "ORCL", "KO", "PEP",
    "CVX", "ADBE", "DIS", "MCD", "CSCO", "ABT", "TMO", "WFC", "LIN", "ACN",
    "INTC", "IBM", "PM", "GE", "NOW", "TXN", "CAT", "QCOM", "AXP", "INTU",
]


def _normalize_wiki_symbol(sym: str) -> str:
    # Wikipedia uses e.g. "BRK.B"; Yahoo Finance wants "BRK-B".
    return sym.strip().upper().replace(".", "-")


def _fetch_sp500_live() -> list:
    resp = requests.get(SP500_WIKI_URL, timeout=config.HTTP_TIMEOUT_SECONDS,
                         headers={"User-Agent": config.USER_AGENT})
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))
    df = tables[0]  # the constituents table is the first one on the page
    out = []
    for _, row in df.iterrows():
        symbol = _normalize_wiki_symbol(str(row["Symbol"]))
        name = str(row.get("Security", symbol))
        out.append({"symbol": symbol, "name": name})
    if len(out) < 400:  # sanity check - Wikipedia's table layout may have changed
        raise ValueError(f"unexpectedly few rows parsed ({len(out)}) - page structure may have changed")
    return out


def get_sp500_tickers() -> list:
    cached = db.get_universe_cache("SP500")
    if cached:
        updated_at = datetime.fromisoformat(cached["updated_at"])
        if datetime.utcnow() - updated_at < timedelta(days=UNIVERSE_CACHE_TTL_DAYS):
            return cached["tickers"]

    try:
        tickers = _fetch_sp500_live()
        db.set_universe_cache("SP500", tickers)
        logger.info("refreshed S&P 500 universe from Wikipedia (%d tickers)", len(tickers))
        return tickers
    except Exception as e:
        logger.warning("live S&P 500 fetch failed (%s), using cache/fallback", e)
        if cached:
            return cached["tickers"]  # stale cache beats the tiny hardcoded fallback
        return [{"symbol": s, "name": s} for s in SP500_FALLBACK]


def get_ta125_tickers() -> list:
    path = DATA_DIR / "ta125_fallback.json"
    try:
        data = json.loads(path.read_text())
        return [{"symbol": t["symbol"], "name": t["name"]} for t in data["tickers"]]
    except Exception as e:
        logger.warning("could not load TA-125 fallback list: %s", e)
        return []


def get_universe(selection: list) -> list:
    """selection: e.g. ["SP500", "TA125"]. Returns [{symbol, name, market}], de-duplicated."""
    out = {}
    if "SP500" in selection:
        for t in get_sp500_tickers():
            out[t["symbol"]] = {"symbol": t["symbol"], "name": t["name"], "market": "US"}
    if "TA125" in selection:
        for t in get_ta125_tickers():
            out[t["symbol"]] = {"symbol": t["symbol"], "name": t["name"], "market": "TASE"}
    return list(out.values())
