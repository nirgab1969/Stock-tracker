"""
High-level push helper: loads/creates the VAPID keypair once, and knows how
to broadcast a notification to every subscribed browser, cleaning up dead
subscriptions along the way.
"""
import logging

from . import vapid as vapid_mod
from . import webpush
from .. import config, db

logger = logging.getLogger(__name__)

_private_key = None
_public_key_b64 = None


def init():
    global _private_key, _public_key_b64
    _private_key, _public_key_b64 = vapid_mod.load_or_create_keys()
    logger.info("VAPID keys ready (public key: %s...)", _public_key_b64[:16])


def get_public_key() -> str:
    if _public_key_b64 is None:
        init()
    return _public_key_b64


def broadcast(title: str, body: str, data: dict = None, ttl: int = 300) -> dict:
    """Sends the same notification to every registered subscription. Returns a small summary."""
    if _private_key is None:
        init()

    payload = {"title": title, "body": body, "data": data or {}}
    subs = db.list_subscriptions()
    sent, failed, removed = 0, 0, 0

    for sub in subs:
        subscription = {"endpoint": sub["endpoint"], "p256dh": sub["p256dh"], "auth": sub["auth"]}
        try:
            webpush.send_push(subscription, payload, _private_key, _public_key_b64,
                               config.VAPID_SUBJECT, ttl=ttl)
            sent += 1
        except webpush.PushSubscriptionGone:
            db.remove_subscription(sub["endpoint"])
            removed += 1
            logger.info("removed dead push subscription")
        except Exception as e:
            failed += 1
            logger.warning("push send failed: %s", e)

    return {"sent": sent, "failed": failed, "removed": removed, "total_subscriptions": len(subs)}
