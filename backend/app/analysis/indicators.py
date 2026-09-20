"""
Technical indicators implemented directly on pandas Series/DataFrames
(no extra dependency on the `ta` package - keeps installs simple).

All functions are defensive about short histories (a newly-listed stock, or a
data glitch that returns fewer rows than a given lookback) - they return NaN
rather than raising, and callers treat NaN as "not enough data yet".
"""
import numpy as np
import pandas as pd

from .. import config


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    # Wilder's smoothing (equivalent to an EMA with alpha = 1/period)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100 - (100 / (1 + rs))
    # If avg_loss is 0 (all gains), RSI is 100
    result = result.where(avg_loss != 0, 100)
    return result


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def bollinger_bands(series: pd.Series, period: int = 20, num_std: float = 2.0):
    mid = sma(series, period)
    std = series.rolling(window=period, min_periods=period).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    return upper, mid, lower


def find_support_resistance(df: pd.DataFrame, lookback: int = 60, window: int = 3):
    """
    Finds the nearest swing-low (support) and swing-high (resistance) below/above
    the current price, using a simple local-extrema scan over the lookback window.
    Returns (support, resistance) - either can be None if none was found.
    """
    recent = df.tail(lookback)
    if len(recent) < window * 2 + 1:
        return None, None

    highs = recent["high"].values
    lows = recent["low"].values
    current_price = recent["close"].iloc[-1]

    swing_lows, swing_highs = [], []
    for i in range(window, len(recent) - window):
        local_low = lows[i - window: i + window + 1]
        local_high = highs[i - window: i + window + 1]
        if lows[i] == local_low.min():
            swing_lows.append(lows[i])
        if highs[i] == local_high.max():
            swing_highs.append(highs[i])

    supports_below = [s for s in swing_lows if s < current_price]
    resistances_above = [r for r in swing_highs if r > current_price]

    support = max(supports_below) if supports_below else (min(swing_lows) if swing_lows else None)
    resistance = min(resistances_above) if resistances_above else (max(swing_highs) if swing_highs else None)
    return support, resistance


def volume_spike(df: pd.DataFrame, period: int = 20, multiplier: float = 1.5) -> bool:
    if len(df) < period + 1:
        return False
    avg_vol = df["volume"].iloc[-(period + 1):-1].mean()
    latest_vol = df["volume"].iloc[-1]
    if avg_vol == 0 or np.isnan(avg_vol):
        return False
    return latest_vol >= avg_vol * multiplier


def compute_all(df: pd.DataFrame) -> dict:
    """
    Runs every indicator on the given OHLCV dataframe and returns the latest
    values plus a handful of derived booleans used by the signal engine.
    """
    close = df["close"]
    out = {}

    out["price"] = float(close.iloc[-1])

    rsi_series = rsi(close, config.RSI_PERIOD)
    out["rsi"] = _safe_last(rsi_series)
    out["rsi_prev"] = _safe_last(rsi_series, 2)

    macd_line, signal_line, hist = macd(close, config.MACD_FAST, config.MACD_SLOW, config.MACD_SIGNAL)
    out["macd"] = _safe_last(macd_line)
    out["macd_signal"] = _safe_last(signal_line)
    out["macd_hist"] = _safe_last(hist)
    out["macd_hist_prev"] = _safe_last(hist, 2)

    sma200 = sma(close, config.SMA_TREND_PERIOD)
    ema20 = ema(close, config.EMA_FAST_PERIOD)
    ema50 = ema(close, config.EMA_SLOW_PERIOD)
    out["sma200"] = _safe_last(sma200)
    out["ema20"] = _safe_last(ema20)
    out["ema50"] = _safe_last(ema50)

    upper, mid, lower = bollinger_bands(close, config.BOLLINGER_PERIOD, config.BOLLINGER_STD)
    out["bb_upper"] = _safe_last(upper)
    out["bb_mid"] = _safe_last(mid)
    out["bb_lower"] = _safe_last(lower)

    support, resistance = find_support_resistance(df, config.SUPPORT_RESISTANCE_LOOKBACK)
    out["support"] = float(support) if support is not None else None
    out["resistance"] = float(resistance) if resistance is not None else None

    out["volume_spike"] = bool(volume_spike(df, multiplier=config.VOLUME_SPIKE_MULTIPLIER))

    # Derived trend/momentum flags
    price = out["price"]
    out["above_sma200"] = _gt(price, out["sma200"])
    out["uptrend"] = out["above_sma200"] is True
    out["macd_bullish_cross"] = (
        out["macd_hist"] is not None and out["macd_hist_prev"] is not None
        and out["macd_hist"] > 0 and out["macd_hist_prev"] <= 0
    )
    out["macd_bearish_cross"] = (
        out["macd_hist"] is not None and out["macd_hist_prev"] is not None
        and out["macd_hist"] < 0 and out["macd_hist_prev"] >= 0
    )
    out["rsi_oversold"] = out["rsi"] is not None and out["rsi"] < config.RSI_OVERSOLD
    out["rsi_recovering_from_oversold"] = (
        out["rsi"] is not None and out["rsi_prev"] is not None
        and out["rsi_prev"] < config.RSI_OVERSOLD <= out["rsi"]
    )
    out["rsi_overbought"] = out["rsi"] is not None and out["rsi"] >= config.RSI_OVERBOUGHT
    out["near_support"] = (
        out["support"] is not None and price <= out["support"] * 1.03
    )
    out["near_resistance"] = (
        out["resistance"] is not None and price >= out["resistance"] * 0.97
    )
    out["broke_below_ema50"] = _gt(out["ema50"], price)  # price fell below ema50

    return out


def _safe_last(series: pd.Series, back: int = 1):
    if series is None or len(series) < back:
        return None
    val = series.iloc[-back]
    if pd.isna(val):
        return None
    return float(val)


def _gt(a, b):
    if a is None or b is None:
        return None
    return a > b
