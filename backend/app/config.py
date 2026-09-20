"""
Central configuration for the stock tracker app.
Tune these values to change trading style, scan frequency and alert sensitivity.
Everything can be overridden with environment variables (see .env.example).
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = os.environ.get("DB_PATH", str(DATA_DIR / "stocktracker.db"))
VAPID_KEYS_PATH = os.environ.get("VAPID_KEYS_PATH", str(DATA_DIR / "vapid_keys.json"))

# Make sure the target directories exist even when DB_PATH / VAPID_KEYS_PATH are
# overridden to point somewhere else (e.g. a mounted persistent disk in production).
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
Path(VAPID_KEYS_PATH).parent.mkdir(parents=True, exist_ok=True)

# Contact info required by the VAPID spec (shown to push services, never to the user).
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:admin@example.com")

# --- Trading style: swing trading (days-to-weeks holding horizon) ---
# Daily candles are used (not intraday) since the app targets swing trading, not day trading.
PRICE_HISTORY_RANGE = "1y"      # how much history to pull per ticker
PRICE_HISTORY_INTERVAL = "1d"   # daily candles

# --- Scan scheduling ---
# How often (minutes) the background scanner re-evaluates the whole watchlist.
# Swing trading doesn't need minute-by-minute scans; every few hours is plenty.
SCAN_INTERVAL_MINUTES = int(os.environ.get("SCAN_INTERVAL_MINUTES", "120"))

# Only scan during these hours (local server time, 24h). Set both to 0/24 to scan around the clock.
SCAN_ACTIVE_HOUR_START = int(os.environ.get("SCAN_ACTIVE_HOUR_START", "7"))
SCAN_ACTIVE_HOUR_END = int(os.environ.get("SCAN_ACTIVE_HOUR_END", "23"))

# --- Scoring weights (must sum to 1.0) ---
WEIGHT_TECHNICAL = float(os.environ.get("WEIGHT_TECHNICAL", "0.45"))
WEIGHT_FUNDAMENTAL = float(os.environ.get("WEIGHT_FUNDAMENTAL", "0.30"))
WEIGHT_SENTIMENT = float(os.environ.get("WEIGHT_SENTIMENT", "0.25"))

# --- Alert thresholds (0-100 composite score) ---
HIGH_POTENTIAL_SCORE_THRESHOLD = float(os.environ.get("HIGH_POTENTIAL_SCORE_THRESHOLD", "72"))

# Minimum hours between two alerts of the same type for the same ticker (avoid spamming).
ALERT_COOLDOWN_HOURS = float(os.environ.get("ALERT_COOLDOWN_HOURS", "12"))

# Technical thresholds tuned for swing trading
RSI_OVERSOLD = 35
RSI_OVERBOUGHT = 68
RSI_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
SMA_TREND_PERIOD = 200   # long-term trend filter
EMA_FAST_PERIOD = 20
EMA_SLOW_PERIOD = 50
BOLLINGER_PERIOD = 20
BOLLINGER_STD = 2
SUPPORT_RESISTANCE_LOOKBACK = 60  # trading days used to find swing highs/lows
VOLUME_SPIKE_MULTIPLIER = 1.5     # volume vs its own 20d average to count as "confirming" a move

# HTTP client
HTTP_TIMEOUT_SECONDS = 12
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

DEBUG = os.environ.get("FLASK_DEBUG", "0") == "1"
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))
