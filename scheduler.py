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
from .analysis import discovery_engine, engine
from .push import service as push_service

logger = logging.getLogger(__name__)

_stop_event = threading.Event()
_thread = None
_discovery_thread = None
_last_scan_summary = {"started_at": None, "finished_at": None, "results": {}}
_last_discovery_summary = {"started_at": None, "finished_at": None, "discovered": []}
_last_discovery_slot = None  # (date_str, hour) of the last completed discovery scan


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


def run_discovery_scan_and_alert() -> dict:
    """Runs the broad-market discovery scan and pushes a DISCOVERY alert for anything new."""
    summary = {"started_at": datetime.utcnow().isoformat(), "finished_at": None, "discovered": []}
    try:
        result = discovery_engine.run_discovery_scan()
    except Exception:
        logger.exception("discovery scan failed")
        summary["finished_at"] = datetime.utcnow().isoformat()
        global _last_discovery_summary
        _last_discovery_summary = summary
        return summary

    for item in result.get("discovered", []):
        symbol = item["symbol"]
        score = item["composite_score"]
        if not _cooldown_ok(symbol, "DISCOVERY"):
            continue
        discovery = db.get_discovery(symbol)
        display = (discovery or {}).get("display_name") or symbol
        reasons = ", ".join(((discovery or {}).get("scan", {}).get("technical_reasons") or [])[:3])
        title = f"🔎 גילוי חדש: {display} ({score:.0f}/100)"
        body = f"מניה שלא ברשימת המעקב שלך עם פוטנציאל גבוה. {reasons}"
        _send_and_log(symbol, "DISCOVERY", title, body, score)
        summary["discovered"].append(item)

    summary["finished_at"] = datetime.utcnow().isoformat()
    summary["universe_size"] = result.get("universe_size")
    summary["candidates_analyzed"] = result.get("candidates_analyzed")
    _last_discovery_summary = summary
    return summary


def get_last_discovery_summary() -> dict:
    return _last_discovery_summary


def _within_active_hours() -> bool:
    hour = datetime.now().hour
    start, end = config.SCAN_ACTIVE_HOUR_START, config.SCAN_ACTIVE_HOUR_END
    if start == end:
        return True
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # wraps past midnight


def _is_weekend() -> bool:
    """Saturday/Sunday (UTC) - both exchanges' weekly close overlaps this window (see
    config.SKIP_WEEKEND_SCANS for the TASE-on-Sunday caveat)."""
    return datetime.utcnow().weekday() in (5, 6)  # Mon=0 ... Sat=5, Sun=6


def _loop():
    logger.info("scheduler thread started (every %s min, active hours %s-%s)",
                config.SCAN_INTERVAL_MINUTES, config.SCAN_ACTIVE_HOUR_START, config.SCAN_ACTIVE_HOUR_END)
    while not _stop_event.is_set():
        try:
            if config.SKIP_WEEKEND_SCANS and _is_weekend():
                logger.info("weekend (exchanges closed), skipping scan")
            elif _within_active_hours():
                run_full_scan()
            else:
                logger.info("outside active hours, skipping scan")
        except Exception:
            logger.exception("scan loop iteration failed")

        _stop_event.wait(config.SCAN_INTERVAL_MINUTES * 60)


def _discovery_loop():
    """
    Wakes periodically and runs the broad-market discovery scan once per configured
    UTC hour slot (config.DISCOVERY_SCAN_HOURS_UTC), so it fires ~twice a day without
    needing a full cron scheduler.
    """
    global _last_discovery_slot
    logger.info("discovery scheduler thread started (hours UTC: %s)", config.DISCOVERY_SCAN_HOURS_UTC)
    check_interval_seconds = 20 * 60
    while not _stop_event.is_set():
        if config.DISCOVERY_ENABLED and not (config.SKIP_WEEKEND_SCANS and _is_weekend()):
            now = datetime.utcnow()
            slot = (now.strftime("%Y-%m-%d"), now.hour)
            if now.hour in config.DISCOVERY_SCAN_HOURS_UTC and slot != _last_discovery_slot:
                try:
                    run_discovery_scan_and_alert()
                except Exception:
                    logger.exception("discovery loop iteration failed")
                _last_discovery_slot = slot

        _stop_event.wait(check_interval_seconds)


def start_background_scheduler():
    global _thread, _discovery_thread
    _stop_event.clear()
    if not (_thread and _thread.is_alive()):
        _thread = threading.Thread(target=_loop, name="scan-scheduler", daemon=True)
        _thread.start()
    if not (_discovery_thread and _discovery_thread.is_alive()):
        _discovery_thread = threading.Thread(target=_discovery_loop, name="discovery-scheduler", daemon=True)
        _discovery_thread.start()


def stop_background_scheduler():
    _stop_event.set()
