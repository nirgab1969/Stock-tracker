"""
Pure scoring / signal logic - no I/O here, so it's easy to unit test.

Three pillars are combined into one 0-100 "potential score":
  - technical  (price action: trend, momentum, volume)
  - fundamental (valuation, growth, profitability, analyst view)
  - sentiment   (recent news tone)

Any pillar that couldn't be computed (missing data) is dropped and the
remaining pillars' weights are rescaled to still sum to 1 - so a ticker with
no analyst coverage still gets a fair score from technicals + sentiment.

Separately, `generate_signals()` looks at the technical indicators (plus
whether the user says they already hold a position) and raises discrete
ENTRY / EXIT signals for swing trading - these are the "point in time"
alerts, distinct from the general potential score.
"""
from .. import config


def technical_score(tech: dict) -> tuple:
    """Returns (score 0-100, list of human-readable reasons)."""
    score = 50.0
    reasons = []

    if tech.get("above_sma200") is True:
        score += 15
        reasons.append("מעל ממוצע 200 יום (מגמה עולה ארוכת טווח)")
    elif tech.get("above_sma200") is False:
        score -= 12
        reasons.append("מתחת לממוצע 200 יום (מגמה שלילית ארוכת טווח)")

    ema20, ema50 = tech.get("ema20"), tech.get("ema50")
    if ema20 is not None and ema50 is not None:
        if ema20 > ema50:
            score += 8
            reasons.append("ממוצע 20 יום מעל ממוצע 50 יום (מומנטום חיובי)")
        else:
            score -= 8

    macd_hist = tech.get("macd_hist")
    if macd_hist is not None:
        score += 10 if macd_hist > 0 else -10
    if tech.get("macd_bullish_cross"):
        score += 8
        reasons.append("חצייה חיובית טרייה של MACD")
    if tech.get("macd_bearish_cross"):
        score -= 10
        reasons.append("חצייה שלילית של MACD")

    rsi = tech.get("rsi")
    if rsi is not None:
        if 40 <= rsi <= 65:
            score += 10
        elif tech.get("rsi_overbought"):
            score -= 15
            reasons.append(f"RSI בקנייה מוגזמת ({rsi:.0f})")
    if tech.get("rsi_recovering_from_oversold"):
        score += 6
        reasons.append("RSI מתאושש מאזור מכירת יתר")

    if tech.get("volume_spike"):
        score += 8
        reasons.append("קפיצת נפח מסחר חריגה")

    if tech.get("near_support"):
        score += 6
        reasons.append("המחיר קרוב לתמיכה")
    if tech.get("near_resistance"):
        score -= 8
        reasons.append("המחיר קרוב להתנגדות")

    return max(0.0, min(100.0, score)), reasons


def composite_score(technical, fundamental, sentiment) -> tuple:
    """
    technical / fundamental / sentiment: each a 0-100 float or None.
    Returns (composite 0-100 or None, weights_used dict).
    """
    weights = {
        "technical": config.WEIGHT_TECHNICAL,
        "fundamental": config.WEIGHT_FUNDAMENTAL,
        "sentiment": config.WEIGHT_SENTIMENT,
    }
    values = {"technical": technical, "fundamental": fundamental, "sentiment": sentiment}
    available = {k: v for k, v in values.items() if v is not None}
    if not available:
        return None, {}

    total_weight = sum(weights[k] for k in available)
    composite = sum(available[k] * weights[k] for k in available) / total_weight
    used = {k: round(weights[k] / total_weight, 3) for k in available}
    return round(composite, 1), used


def generate_signals(tech: dict, position: dict = None) -> dict:
    """
    position: {"in_position": bool, "entry_price": float|None} or None.
    Returns entry/exit signal booleans with reasons, for swing-trading use.
    """
    position = position or {"in_position": False, "entry_price": None}
    entry_reasons, exit_reasons = [], []

    uptrend = tech.get("above_sma200") is True
    pullback_zone = tech.get("near_support") or (
        tech.get("ema50") and tech.get("price") and tech["price"] <= tech["ema50"] * 1.02
    )
    momentum_turning_up = tech.get("macd_bullish_cross") or (
        tech.get("macd_hist") is not None and tech.get("macd_hist_prev") is not None
        and tech["macd_hist"] > tech["macd_hist_prev"]
    )
    reversal_from_oversold = tech.get("rsi_recovering_from_oversold") or tech.get("rsi_oversold")

    entry_signal = False
    if uptrend and pullback_zone and momentum_turning_up and reversal_from_oversold:
        entry_signal = True
        entry_reasons = [
            "מגמה עולה (מעל ממוצע 200 יום)",
            "המחיר בתיקון לאזור תמיכה / ממוצע 50",
            "מומנטום ה-MACD מתחיל להסתובב למעלה",
            "RSI מתאושש מאזור מכירת יתר",
        ]

    breakout_entry = False
    if tech.get("near_resistance") and tech.get("volume_spike") and (tech.get("macd_hist") or 0) > 0:
        # price pushing into/through resistance with volume + positive momentum
        breakout_entry = True
        entry_signal = True
        entry_reasons = entry_reasons or [
            "פריצת אזור התנגדות בנפח מסחר גבוה",
            "מומנטום MACD חיובי מאשש את הפריצה",
        ]

    exit_signal = False
    warning_kind = None  # 'weakening_upside' (RSI/resistance) or 'trend_break' (MACD/EMA50 break)
    if tech.get("rsi_overbought"):
        exit_signal = True
        warning_kind = "weakening_upside"
        exit_reasons.append(f"RSI בקנייה מוגזמת ({tech.get('rsi'):.0f})")
    if tech.get("near_resistance") and not breakout_entry:
        exit_signal = True
        warning_kind = warning_kind or "weakening_upside"
        exit_reasons.append("המחיר מתקרב להתנגדות משמעותית")
    if tech.get("macd_bearish_cross"):
        exit_signal = True
        warning_kind = warning_kind or "trend_break"
        exit_reasons.append("חצייה שלילית של MACD - מומנטום מתחלף")
    if tech.get("broke_below_ema50") and tech.get("above_sma200") is False:
        exit_signal = True
        warning_kind = "trend_break"
        exit_reasons.append("שבירת ממוצע 50 יום מטה בתוך מגמה שלילית")

    # If the user is holding a position, add P&L context and decide whether this
    # is a "lock in profit" moment or a "cut the loss" moment based on actual P&L,
    # not just which technical condition fired.
    pnl_pct = None
    exit_type = None
    if position.get("in_position") and position.get("entry_price") and tech.get("price"):
        pnl_pct = round((tech["price"] - position["entry_price"]) / position["entry_price"] * 100, 2)
        if pnl_pct <= -8 and not exit_signal:
            exit_signal = True
            warning_kind = "trend_break"
            exit_reasons.append(f"הפסד של {pnl_pct}% מנקודת הכניסה - שווה לשקול הגבלת הפסד")
        if exit_signal:
            exit_type = "take_profit" if pnl_pct >= 0 else "stop_loss"
    elif exit_signal:
        # No position tracked - report the technical nature of the warning instead of P&L.
        exit_type = "take_profit" if warning_kind == "weakening_upside" else "stop_loss"

    return {
        "entry_signal": entry_signal,
        "entry_reasons": entry_reasons,
        "breakout_entry": breakout_entry,
        "exit_signal": exit_signal,
        "exit_reasons": exit_reasons,
        "exit_type": exit_type,
        "pnl_pct": pnl_pct,
    }


def potential_label(score):
    if score is None:
        return "לא ידוע"
    if score >= config.HIGH_POTENTIAL_SCORE_THRESHOLD:
        return "פוטנציאל גבוה"
    if score >= 55:
        return "פוטנציאל בינוני"
    return "פוטנציאל נמוך"
