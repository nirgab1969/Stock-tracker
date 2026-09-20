"""
Market data provider.

Works two ways:
  1. If the optional `yfinance` package is installed, we use it - it deals
     with Yahoo Finance's cookie/crumb anti-bot requirements far more
     reliably than raw HTTP calls.
  2. Otherwise we fall back to calling Yahoo Finance's public (unofficial)
     endpoints directly with `requests`, with defensive error handling so a
     single flaky ticker never crashes a whole scan.

Supports both US tickers (AAPL, MSFT, ...) and TASE tickers (use the ".TA"
suffix, e.g. TEVA.TA, POLI.TA, ICL.TA - this is how Yahoo Finance lists the
Tel Aviv Stock Exchange).
"""
import logging
import time
import xml.etree.ElementTree as ET

import pandas as pd
import requests

from . import config

logger = logging.getLogger(__name__)

try:
    import yfinance as yf
    HAVE_YFINANCE = True
except ImportError:
    HAVE_YFINANCE = False

_session = requests.Session()
_session.headers.update({"User-Agent": config.USER_AGENT})

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
QUOTE_SUMMARY_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
NEWS_RSS_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline"

FUNDAMENTAL_MODULES = "defaultKeyStatistics,financialData,summaryDetail,price,recommendationTrend"


class MarketDataError(Exception):
    pass


def get_history(symbol: str) -> pd.DataFrame:
    """Returns a DataFrame with columns: open, high, low, close, volume - indexed by date."""
    if HAVE_YFINANCE:
        try:
            df = yf.Ticker(symbol).history(
                period=config.PRICE_HISTORY_RANGE, interval=config.PRICE_HISTORY_INTERVAL
            )
            if df is not None and not df.empty:
                df = df.rename(columns=str.lower)
                return df[["open", "high", "low", "close", "volume"]].dropna()
        except Exception as e:
            logger.warning("yfinance history failed for %s (%s), falling back to raw API", symbol, e)

    return _get_history_raw(symbol)


def get_history_batch(symbols: list) -> dict:
    """
    Fetches OHLCV history for many symbols as efficiently as possible - used by the
    discovery scanner's stage 1 (technical-only pre-filter over a whole index).
    Returns {symbol: DataFrame}; symbols that fail to fetch are simply omitted.
    """
    if HAVE_YFINANCE and symbols:
        try:
            raw = yf.download(
                tickers=symbols, period=config.PRICE_HISTORY_RANGE, interval=config.PRICE_HISTORY_INTERVAL,
                group_by="ticker", threads=True, progress=False, auto_adjust=False,
            )
            out = {}
            for sym in symbols:
                try:
                    sub = raw[sym] if len(symbols) > 1 else raw
                    sub = sub.rename(columns=str.lower).dropna()
                    if not sub.empty and len(sub) >= 30:
                        out[sym] = sub[["open", "high", "low", "close", "volume"]]
                except Exception:
                    continue
            if out:
                return out
        except Exception as e:
            logger.warning("yfinance batch download failed (%s), falling back to one-by-one", e)

    # Fallback: sequential raw calls (slower, but the discovery scan only runs a couple
    # times a day so this is an acceptable worst case when yfinance isn't installed).
    out = {}
    for sym in symbols:
        try:
            out[sym] = _get_history_raw(sym)
        except MarketDataError:
            continue
        sleep_between_requests()
    return out


def _get_history_raw(symbol: str) -> pd.DataFrame:
    try:
        resp = _session.get(
            CHART_URL.format(symbol=symbol),
            params={"range": config.PRICE_HISTORY_RANGE, "interval": config.PRICE_HISTORY_INTERVAL},
            timeout=config.HTTP_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]
        df = pd.DataFrame(
            {
                "open": quote["open"],
                "high": quote["high"],
                "low": quote["low"],
                "close": quote["close"],
                "volume": quote["volume"],
            },
            index=pd.to_datetime(timestamps, unit="s"),
        )
        return df.dropna()
    except Exception as e:
        raise MarketDataError(f"could not fetch history for {symbol}: {e}") from e


def get_fundamentals(symbol: str) -> dict:
    """Best-effort fundamentals dict. Missing fields are simply absent (scoring handles that)."""
    if HAVE_YFINANCE:
        try:
            info = yf.Ticker(symbol).get_info()
            if info:
                return _normalize_yfinance_info(info)
        except Exception as e:
            logger.warning("yfinance fundamentals failed for %s (%s), falling back to raw API", symbol, e)

    return _get_fundamentals_raw(symbol)


def _normalize_yfinance_info(info: dict) -> dict:
    return {
        "trailingPE": info.get("trailingPE"),
        "forwardPE": info.get("forwardPE"),
        "priceToBook": info.get("priceToBook"),
        "debtToEquity": info.get("debtToEquity"),
        "revenueGrowth": info.get("revenueGrowth"),
        "earningsGrowth": info.get("earningsGrowth"),
        "profitMargins": info.get("profitMargins"),
        "returnOnEquity": info.get("returnOnEquity"),
        "recommendationMean": info.get("recommendationMean"),
        "targetMeanPrice": info.get("targetMeanPrice"),
        "currentPrice": info.get("currentPrice") or info.get("regularMarketPrice"),
        "shortName": info.get("shortName") or info.get("longName"),
    }


def _get_fundamentals_raw(symbol: str) -> dict:
    try:
        resp = _session.get(
            QUOTE_SUMMARY_URL.format(symbol=symbol),
            params={"modules": FUNDAMENTAL_MODULES},
            timeout=config.HTTP_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        result = resp.json()["quoteSummary"]["result"][0]
        stats = result.get("defaultKeyStatistics", {})
        fin = result.get("financialData", {})
        summary = result.get("summaryDetail", {})
        price = result.get("price", {})
        rec = result.get("recommendationTrend", {})

        def raw(section, key):
            v = section.get(key)
            if isinstance(v, dict):
                return v.get("raw")
            return v

        return {
            "trailingPE": raw(summary, "trailingPE"),
            "forwardPE": raw(summary, "forwardPE"),
            "priceToBook": raw(stats, "priceToBook"),
            "debtToEquity": raw(fin, "debtToEquity"),
            "revenueGrowth": raw(fin, "revenueGrowth"),
            "earningsGrowth": raw(stats, "earningsQuarterlyGrowth"),
            "profitMargins": raw(fin, "profitMargins"),
            "returnOnEquity": raw(fin, "returnOnEquity"),
            "recommendationMean": raw(fin, "recommendationMean"),
            "targetMeanPrice": raw(fin, "targetMeanPrice"),
            "currentPrice": raw(price, "regularMarketPrice"),
            "shortName": price.get("shortName") or price.get("longName"),
        }
    except Exception as e:
        logger.warning("could not fetch fundamentals for %s: %s", symbol, e)
        return {}


def get_news(symbol: str, limit: int = 8) -> list:
    """Returns a list of {title, publisher, link} for recent headlines about the ticker."""
    if HAVE_YFINANCE:
        try:
            items = yf.Ticker(symbol).news or []
            out = []
            for it in items[:limit]:
                content = it.get("content", it)  # newer yfinance nests under "content"
                title = content.get("title") or it.get("title")
                if title:
                    out.append({
                        "title": title,
                        "publisher": (content.get("provider") or {}).get("displayName", "")
                                     if isinstance(content.get("provider"), dict) else it.get("publisher", ""),
                        "link": (content.get("canonicalUrl") or {}).get("url", "") if isinstance(content.get("canonicalUrl"), dict) else it.get("link", ""),
                    })
            if out:
                return out
        except Exception as e:
            logger.warning("yfinance news failed for %s (%s), falling back to RSS", symbol, e)

    return _get_news_rss(symbol, limit)


def _get_news_rss(symbol: str, limit: int = 8) -> list:
    try:
        resp = _session.get(
            NEWS_RSS_URL, params={"s": symbol, "region": "US", "lang": "en-US"},
            timeout=config.HTTP_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        out = []
        for item in root.iter("item"):
            title_el = item.find("title")
            link_el = item.find("link")
            if title_el is not None and title_el.text:
                out.append({"title": title_el.text, "publisher": "Yahoo Finance", "link": link_el.text if link_el is not None else ""})
            if len(out) >= limit:
                break
        return out
    except Exception as e:
        logger.warning("could not fetch news for %s: %s", symbol, e)
        return []


def sleep_between_requests():
    """Small politeness delay so we don't hammer Yahoo's endpoints during a full-watchlist scan."""
    time.sleep(0.6)
