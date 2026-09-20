"""
Lightweight SQLite storage layer (no ORM dependency needed).
Tables:
  watchlist          - tickers the user wants tracked, with optional position info
  push_subscriptions - browser Web Push subscriptions (one per installed device/browser)
  alert_log          - history of alerts sent, used for cooldown + the "history" screen
  scan_cache         - last computed analysis per ticker, served to the dashboard instantly
  universe_cache     - cached ticker-universe lists (e.g. S&P 500) for the discovery scanner
  discoveries        - high-potential tickers found by the broad discovery scan (not on the watchlist)
"""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

from . import config

_local = threading.local()


def _connect():
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_conn():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL UNIQUE,
                display_name TEXT,
                market TEXT DEFAULT 'US',
                in_position INTEGER DEFAULT 0,
                entry_price REAL,
                shares REAL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint TEXT NOT NULL UNIQUE,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS alert_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                alert_type TEXT NOT NULL,     -- HIGH_POTENTIAL | ENTRY | EXIT
                message TEXT NOT NULL,
                score REAL,
                sent_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scan_cache (
                symbol TEXT PRIMARY KEY,
                payload TEXT NOT NULL,   -- JSON blob of the full analysis result
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS universe_cache (
                key TEXT PRIMARY KEY,        -- e.g. "SP500"
                tickers_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS discoveries (
                symbol TEXT PRIMARY KEY,
                display_name TEXT,
                market TEXT DEFAULT 'US',
                composite_score REAL,
                payload TEXT NOT NULL,       -- JSON blob of the full analysis result
                status TEXT DEFAULT 'new',   -- new | promoted | dismissed
                discovered_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )


# ---------- watchlist ----------

def add_symbol(symbol, display_name=None, market="US", in_position=False, entry_price=None, shares=None):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO watchlist (symbol, display_name, market, in_position, entry_price, shares, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(symbol) DO UPDATE SET
                 display_name=excluded.display_name,
                 market=excluded.market,
                 in_position=excluded.in_position,
                 entry_price=excluded.entry_price,
                 shares=excluded.shares
            """,
            (symbol.upper(), display_name, market, int(in_position), entry_price, shares,
             datetime.utcnow().isoformat()),
        )


def remove_symbol(symbol):
    with get_conn() as conn:
        conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol.upper(),))
        conn.execute("DELETE FROM scan_cache WHERE symbol = ?", (symbol.upper(),))


def list_watchlist():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM watchlist ORDER BY symbol").fetchall()
        return [dict(r) for r in rows]


def update_position(symbol, in_position, entry_price=None, shares=None):
    with get_conn() as conn:
        conn.execute(
            "UPDATE watchlist SET in_position=?, entry_price=?, shares=? WHERE symbol=?",
            (int(in_position), entry_price, shares, symbol.upper()),
        )


# ---------- push subscriptions ----------

def add_subscription(endpoint, p256dh, auth):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO push_subscriptions (endpoint, p256dh, auth, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(endpoint) DO UPDATE SET p256dh=excluded.p256dh, auth=excluded.auth""",
            (endpoint, p256dh, auth, datetime.utcnow().isoformat()),
        )


def remove_subscription(endpoint):
    with get_conn() as conn:
        conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))


def list_subscriptions():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM push_subscriptions").fetchall()
        return [dict(r) for r in rows]


# ---------- alert log ----------

def log_alert(symbol, alert_type, message, score=None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO alert_log (symbol, alert_type, message, score, sent_at) VALUES (?, ?, ?, ?, ?)",
            (symbol.upper(), alert_type, message, score, datetime.utcnow().isoformat()),
        )


def last_alert_time(symbol, alert_type):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT sent_at FROM alert_log WHERE symbol=? AND alert_type=? ORDER BY sent_at DESC LIMIT 1",
            (symbol.upper(), alert_type),
        ).fetchone()
        return row["sent_at"] if row else None


def recent_alerts(limit=50):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM alert_log ORDER BY sent_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- scan cache ----------

def save_scan_result(symbol, payload: dict):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO scan_cache (symbol, payload, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(symbol) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at""",
            (symbol.upper(), json.dumps(payload), datetime.utcnow().isoformat()),
        )


def get_scan_result(symbol):
    with get_conn() as conn:
        row = conn.execute("SELECT payload, updated_at FROM scan_cache WHERE symbol=?", (symbol.upper(),)).fetchone()
        if not row:
            return None
        data = json.loads(row["payload"])
        data["_updated_at"] = row["updated_at"]
        return data


def all_scan_results():
    with get_conn() as conn:
        rows = conn.execute("SELECT symbol, payload, updated_at FROM scan_cache").fetchall()
        out = {}
        for r in rows:
            data = json.loads(r["payload"])
            data["_updated_at"] = r["updated_at"]
            out[r["symbol"]] = data
        return out


# ---------- universe cache (discovery scanner) ----------

def get_universe_cache(key: str):
    with get_conn() as conn:
        row = conn.execute("SELECT tickers_json, updated_at FROM universe_cache WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        return {"tickers": json.loads(row["tickers_json"]), "updated_at": row["updated_at"]}


def set_universe_cache(key: str, tickers: list):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO universe_cache (key, tickers_json, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET tickers_json=excluded.tickers_json, updated_at=excluded.updated_at""",
            (key, json.dumps(tickers), datetime.utcnow().isoformat()),
        )


# ---------- discoveries (broad-market scan results) ----------

def upsert_discovery(symbol, display_name, market, composite_score, payload: dict):
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        existing = conn.execute("SELECT status, discovered_at FROM discoveries WHERE symbol=?", (symbol.upper(),)).fetchone()
        if existing and existing["status"] == "dismissed":
            return  # respect the user's earlier dismissal - don't resurrect it automatically
        discovered_at = existing["discovered_at"] if existing else now
        conn.execute(
            """INSERT INTO discoveries (symbol, display_name, market, composite_score, payload, status, discovered_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'new', ?, ?)
               ON CONFLICT(symbol) DO UPDATE SET
                 display_name=excluded.display_name, market=excluded.market,
                 composite_score=excluded.composite_score, payload=excluded.payload,
                 updated_at=excluded.updated_at""",
            (symbol.upper(), display_name, market, composite_score, json.dumps(payload), discovered_at, now),
        )


def list_discoveries(status: str = None):
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM discoveries WHERE status=? ORDER BY composite_score DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM discoveries ORDER BY composite_score DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["scan"] = json.loads(d.pop("payload"))
            out.append(d)
        return out


def get_discovery(symbol: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM discoveries WHERE symbol=?", (symbol.upper(),)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["scan"] = json.loads(d.pop("payload"))
        return d


def set_discovery_status(symbol: str, status: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE discoveries SET status=?, updated_at=? WHERE symbol=?",
            (status, datetime.utcnow().isoformat(), symbol.upper()),
        )
