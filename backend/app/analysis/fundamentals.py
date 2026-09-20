"""
Fundamental scoring.

Turns the raw fundamentals dict from market_data.get_fundamentals() into a
single 0-100 "fundamental strength" score. Every sub-metric is optional -
Yahoo doesn't always return all of them (especially for smaller TASE
tickers), so the final score is the average of whatever sub-scores *could*
be computed. If nothing at all was available, the score is None and the
composite scoring engine simply redistributes its weight to the other two
pillars (technical + sentiment) instead of guessing.
"""


def _clamp(x, lo=0, hi=100):
    return max(lo, min(hi, x))


def _score_pe(pe):
    if pe is None or pe <= 0:
        return 25  # negative/no earnings -> penalize, but not to zero (could still be a turnaround story)
    if pe < 10:
        return 75
    if pe <= 25:
        return 90
    if pe <= 40:
        return 55
    return 25


def _score_pb(pb):
    if pb is None:
        return None
    if pb <= 0:
        return 40
    if pb < 1:
        return 90
    if pb <= 3:
        return 75
    if pb <= 6:
        return 50
    return 25


def _score_debt_to_equity(de):
    if de is None:
        return None
    # yfinance/quoteSummary report this roughly as a percentage (e.g. 45 == 0.45x)
    if de < 50:
        return 90
    if de < 100:
        return 65
    if de < 200:
        return 40
    return 20


def _score_growth(g):
    if g is None:
        return None
    return _clamp(50 + g * 200)


def _score_margin(m):
    if m is None:
        return None
    return _clamp(m * 300 + 30)


def _score_roe(roe):
    if roe is None:
        return None
    return _clamp(roe * 300 + 20)


def _score_analyst_recommendation(mean_rating):
    if mean_rating is None:
        return None
    # Yahoo scale: 1 = Strong Buy ... 5 = Strong Sell
    return _clamp(100 - (mean_rating - 1) * 25)


def _score_analyst_upside(current_price, target_mean_price):
    if not current_price or not target_mean_price:
        return None
    upside_pct = (target_mean_price - current_price) / current_price
    return _clamp(50 + upside_pct * 100)


def analyze_fundamentals(fundamentals: dict) -> dict:
    if not fundamentals:
        return {"score": None, "details": {}, "available_metrics": 0}

    sub_scores = {
        "valuation_pe": _score_pe(fundamentals.get("trailingPE") or fundamentals.get("forwardPE")),
        "valuation_pb": _score_pb(fundamentals.get("priceToBook")),
        "debt_to_equity": _score_debt_to_equity(fundamentals.get("debtToEquity")),
        "revenue_growth": _score_growth(fundamentals.get("revenueGrowth")),
        "earnings_growth": _score_growth(fundamentals.get("earningsGrowth")),
        "profit_margin": _score_margin(fundamentals.get("profitMargins")),
        "return_on_equity": _score_roe(fundamentals.get("returnOnEquity")),
        "analyst_recommendation": _score_analyst_recommendation(fundamentals.get("recommendationMean")),
        "analyst_upside": _score_analyst_upside(
            fundamentals.get("currentPrice"), fundamentals.get("targetMeanPrice")
        ),
    }

    available = {k: v for k, v in sub_scores.items() if v is not None}
    if not available:
        return {"score": None, "details": sub_scores, "available_metrics": 0}

    score = sum(available.values()) / len(available)
    return {"score": round(score, 1), "details": sub_scores, "available_metrics": len(available)}
