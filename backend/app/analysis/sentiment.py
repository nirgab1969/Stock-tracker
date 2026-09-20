"""
News sentiment scoring.

Uses VADER (vaderSentiment) if it's installed - it's a well-tested lexicon
tuned for short, informal text (headlines fit that profile well). If it is
not installed, falls back to a small built-in finance-flavoured lexicon so
the feature still works with zero extra dependencies.
"""
import re

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    _vader = SentimentIntensityAnalyzer()
    HAVE_VADER = True
except ImportError:
    _vader = None
    HAVE_VADER = False


_POSITIVE_WORDS = {
    "beat", "beats", "beating", "surge", "surges", "surging", "soar", "soars", "soaring",
    "rally", "rallies", "rallying", "jump", "jumps", "record", "growth", "grow", "grows",
    "growing", "upgrade", "upgraded", "outperform", "bullish", "profit", "profits",
    "profitable", "gain", "gains", "gaining", "strong", "stronger", "strength", "buy",
    "buyback", "buybacks", "dividend", "raise", "raised", "raising", "expansion",
    "expand", "expands", "win", "wins", "winning", "approval", "approved", "breakthrough",
    "innovation", "partnership", "deal", "acquire", "acquisition", "boost", "boosts",
    "optimistic", "optimism", "exceed", "exceeds", "exceeded", "high", "highs", "top",
    "positive", "success", "successful", "milestone", "up", "rise", "rises", "rising",
}

_NEGATIVE_WORDS = {
    "miss", "misses", "missed", "plunge", "plunges", "plunging", "crash", "crashes",
    "crashing", "slump", "slumps", "slide", "slides", "sliding", "downgrade",
    "downgraded", "underperform", "bearish", "loss", "losses", "losing", "weak",
    "weaker", "weakness", "sell", "selloff", "sell-off", "cut", "cuts", "cutting",
    "layoff", "layoffs", "lawsuit", "investigation", "probe", "fraud", "recall",
    "bankruptcy", "default", "decline", "declines", "declining", "drop", "drops",
    "dropping", "fall", "falls", "falling", "concern", "concerns", "risk", "risks",
    "risky", "warning", "warns", "negative", "fail", "fails", "failure", "delay",
    "delayed", "delays", "scandal", "fine", "fined", "penalty", "low", "lows",
    "worst", "down", "tumble", "tumbles", "tumbling", "recession", "volatility",
}

_WORD_RE = re.compile(r"[A-Za-z']+")


def _lexicon_score(text: str) -> float:
    words = [w.lower() for w in _WORD_RE.findall(text)]
    if not words:
        return 0.0
    pos = sum(1 for w in words if w in _POSITIVE_WORDS)
    neg = sum(1 for w in words if w in _NEGATIVE_WORDS)
    if pos == 0 and neg == 0:
        return 0.0
    raw = (pos - neg) / max(1, (pos + neg))
    return max(-1.0, min(1.0, raw))


def _score_headline(text: str) -> float:
    if HAVE_VADER:
        return _vader.polarity_scores(text)["compound"]
    return _lexicon_score(text)


def analyze_sentiment(news_items: list) -> dict:
    """
    news_items: list of {"title": ..., "publisher": ..., "link": ...}
    Returns {score (0-100 or None if no news), compound_avg, headline_count, headlines: [...]}
    """
    if not news_items:
        return {"score": None, "compound_avg": None, "headline_count": 0, "headlines": []}

    scored = []
    for item in news_items:
        title = item.get("title", "")
        if not title:
            continue
        compound = _score_headline(title)
        scored.append({"title": title, "publisher": item.get("publisher", ""), "link": item.get("link", ""),
                       "compound": round(compound, 3)})

    if not scored:
        return {"score": None, "compound_avg": None, "headline_count": 0, "headlines": []}

    compound_avg = sum(s["compound"] for s in scored) / len(scored)
    score = round((compound_avg + 1) * 50, 1)  # map [-1, 1] -> [0, 100]
    return {
        "score": score,
        "compound_avg": round(compound_avg, 3),
        "headline_count": len(scored),
        "headlines": scored,
    }
