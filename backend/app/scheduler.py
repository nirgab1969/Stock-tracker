"""
Background scan loop. Deliberately a plain thread + sleep loop rather than
APScheduler - it does everything this app needs (run a job every N minutes,
skip outside active hours) with zero extra dependency.
"""
import logging
import threading
import time
from datetime import datetime, timedelta

from . import config, db
from .analysis import engine
from .push import service as push_service

logger = logging.getLogger(__name__)

_stop_event = threading.Event()
_thread = None
_last_scan_summary = {"started_at": None, "finished_at": None, "results": {}}


def run_full_scan() -> dict:
    """Runs one pass over the whole watchlist. Safe to call directly (e.g. from an API endpoint)."""
    watchlist = db.list_watchlist()
    logger.info("starting scan of %d symbols", len(watchlist))
    summary = {"started_at": datetime.utcnow().isoformat(), "finished_at": None, "results": {}}

    for row in watchlist:
        try:
            result = engine.analyze_symbol(row)
        except Exception as e:
            logger.exception("unexpected error analyzing %s", row["symbol"])
            result = {"symbol": row["symbol"], "error": str(e)}

        db.save_scan_result(result["symbol"], result)
        summary["results"][result["symbol"]] = {
            "composite_score": result.get("composite_score"),
            "entry_signal": (result.get("signals") or {}).get("entry_signal"),
            "exit_signal": (result.get("signals") or {}).get("exit_signal"),
            "error": result.get("error"),
        }

        _maybe_alert(result)

        if row is not watchlist[-1]:
            engine.market_data.sleep_between_requests()

    summary["finished_at"] = datetime.utcnow().isoformat()
    global _last_scan_summary
    _last_scan_summary = summary
    logger.info("scan complete")
    return summary


def _cooldown_ok(symbol: str, alert_type: str) -> bool:
    last = db.last_alert_time(symbol, alert_type)
    if not last:
        return True
    last_dt = datetime.fromisoformat(last)
    return datetime.utcnow() - last_dt >= timedelta(hours=config.ALERT_COOLDOWN_HOURS)


def _maybe_alert(result: dict):
    symbol = result.get("symbol")
    if not symbol or result.get("error"):
        return

    display = result.get("display_name") or symbol
    composite = result.get("composite_score")
    signals = result.get("signals") or {}

    # 1) High potential score
    if composite is not None and composite >= config.HIGH_POTENTIAL_SCORE_THRESHOLD:
        if _cooldown_ok(symbol, "HIGH_POTENTIAL"):
            title = f"{display}: פוטנציאל גבוה ({composite:.0f}/100)"
            reasons = ", ".join((result.get("technical_reasons") or [])[:3])
            body = f"ניקוד משוקלל {composite:.0f}/100. {reasons}"
            _send_and_log(symbol, "HIGH_POTENTIAL", title, body, composite)

    # 2) Entry signal
    if signals.get("entry_signal") and _cooldown_ok(symbol, "ENTRY"):
        title = f"{display}: איתות כניסה אפשרי"
        body = "; ".join(signals.get("entry_reasons") or []) or "תנאים טכניים נוחים לכניסה"
        _send_and_log(symbol, "ENTRY", title, body, composite)

    # 3) Exit signal (only meaningful noise-wise if the user actually holds/watches for it)
    if signals.get("exit_signal") and _cooldown_ok(symbol, "EXIT"):
        kind = "מימוש רווח" if signals.get("exit_type") == "take_profit" else "בלימת הפסד / היחלשות מגמה"
        title = f"{display}: איתות יציאה ({kind})"
        pnl = signals.get("pnl_pct")
        pnl_txt = f" | תשואה נוכחית מנקודת הכניסה: {pnl}%" if pnl is not None else ""
        body = ("; ".join(signals.get("exit_reasons") or []) or "שינוי במומנטום הטכני") + pnl_txt
        _send_and_log(symbol, "EXIT", title, body, composite)


def _send_and_log(symbol, alert_type, title, body, score):
    db.log_alert(symbol, alert_type, f"{title} | {body}", score)
    try:
        push_service.broadcast(title, body, data={"symbol": symbol, "alert_type": alert_type})
    except Exception:
        logger.exception("failed to broadcast push for %s/%s", symbol, alert_type)


def get_last_scan_summary() -> dict:
    return _last_scan_summary


def _within_active_hours() -> bool:
    hour = datetime.now().hour
    start, end = config.SCAN_ACTIVE_HOUR_START, config.SCAN_ACTIVE_HOUR_END
    if start == end:
        return True
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # wraps past midnight


def _loop():
    logger.info("scheduler thread started (every %s min, active hours %s-%s)",
                config.SCAN_INTERVAL_MINUTES, config.SCAN_ACTIVE_HOUR_START, config.SCAN_ACTIVE_HOUR_END)
    while not _stop_event.is_set():
        try:
            if _within_active_hours():
                run_full_scan()
            else:
                logger.info("outside active hours, skipping scan")
        except Exception:
            logger.exception("scan loop iteration failed")

        _stop_event.wait(config.SCAN_INTERVAL_MINUTES * 60)


def start_background_scheduler():
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, name="scan-scheduler", daemon=True)
    _thread.start()


def stop_background_scheduler():
    _stop_event.set()
