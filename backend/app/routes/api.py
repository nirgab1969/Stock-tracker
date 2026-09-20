import logging

from flask import Blueprint, jsonify, request

from .. import db, scheduler
from ..push import service as push_service

logger = logging.getLogger(__name__)
api_bp = Blueprint("api", __name__, url_prefix="/api")


# ---------- watchlist ----------

@api_bp.get("/watchlist")
def get_watchlist():
    return jsonify(db.list_watchlist())


@api_bp.post("/watchlist")
def add_watchlist():
    payload = request.get_json(force=True) or {}
    symbol = (payload.get("symbol") or "").strip().upper()
    if not symbol:
        return jsonify({"error": "symbol is required"}), 400

    db.add_symbol(
        symbol=symbol,
        display_name=payload.get("display_name") or symbol,
        market=payload.get("market", "US"),
        in_position=bool(payload.get("in_position", False)),
        entry_price=payload.get("entry_price"),
        shares=payload.get("shares"),
    )
    return jsonify({"ok": True, "symbol": symbol}), 201


@api_bp.delete("/watchlist/<symbol>")
def delete_watchlist(symbol):
    db.remove_symbol(symbol)
    return jsonify({"ok": True})


@api_bp.patch("/watchlist/<symbol>/position")
def update_watchlist_position(symbol):
    payload = request.get_json(force=True) or {}
    db.update_position(
        symbol,
        in_position=bool(payload.get("in_position", False)),
        entry_price=payload.get("entry_price"),
        shares=payload.get("shares"),
    )
    return jsonify({"ok": True})


# ---------- dashboard / scans ----------

@api_bp.get("/dashboard")
def get_dashboard():
    watchlist = db.list_watchlist()
    scans = db.all_scan_results()
    items = []
    for row in watchlist:
        scan = scans.get(row["symbol"].upper(), {})
        items.append({**row, "scan": scan})
    return jsonify({"items": items, "last_scan": scheduler.get_last_scan_summary()})


@api_bp.post("/scan")
def trigger_scan():
    summary = scheduler.run_full_scan()
    return jsonify(summary)


@api_bp.get("/alerts")
def get_alerts():
    limit = int(request.args.get("limit", 50))
    return jsonify(db.recent_alerts(limit))


# ---------- push ----------

@api_bp.get("/push/vapid-public-key")
def vapid_public_key():
    return jsonify({"publicKey": push_service.get_public_key()})


@api_bp.post("/push/subscribe")
def push_subscribe():
    payload = request.get_json(force=True) or {}
    endpoint = payload.get("endpoint")
    keys = payload.get("keys") or {}
    if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
        return jsonify({"error": "invalid subscription payload"}), 400

    db.add_subscription(endpoint, keys["p256dh"], keys["auth"])
    return jsonify({"ok": True}), 201


@api_bp.post("/push/unsubscribe")
def push_unsubscribe():
    payload = request.get_json(force=True) or {}
    endpoint = payload.get("endpoint")
    if endpoint:
        db.remove_subscription(endpoint)
    return jsonify({"ok": True})


@api_bp.post("/push/test")
def push_test():
    result = push_service.broadcast(
        "בדיקת התראות",
        "אם אתה רואה את זה, ההתראות שלך מוגדרות נכון! 🎉",
    )
    return jsonify(result)
