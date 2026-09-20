"""
Web Push payload encryption (RFC 8291 - aes128gcm content coding) and the
HTTP call that actually delivers a notification to a subscribed browser.

This re-implements what the `pywebpush` library normally does, using only
`cryptography` + `requests` + `PyJWT` (all already available) so the app has
no extra native/binary dependency to install for push notifications to work.
"""
import hashlib
import hmac
import logging
import os

import requests
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import vapid as vapid_mod
from .. import config

logger = logging.getLogger(__name__)


def _b64url_decode(s: str) -> bytes:
    import base64
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def _b64url_encode(b: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _hmac_sha256(key: bytes, msg: bytes) -> bytes:
    return hmac.new(key, msg, hashlib.sha256).digest()


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    """HKDF (RFC 5869) - single-block expand is enough since we only ever need <= 32 bytes."""
    prk = _hmac_sha256(salt, ikm)
    t = _hmac_sha256(prk, info + b"\x01")
    return t[:length]


class PushError(Exception):
    pass


class PushSubscriptionGone(PushError):
    """Raised on HTTP 404/410 - the subscription is dead and should be deleted."""


def encrypt_payload(plaintext: bytes, p256dh_b64: str, auth_b64: str):
    """
    Implements RFC 8291 encryption for a Web Push message.
    Returns (encrypted_body: bytes, headers: dict) - headers still need the
    VAPID Authorization header merged in by the caller.
    """
    ua_public_bytes = _b64url_decode(p256dh_b64)
    auth_secret = _b64url_decode(auth_b64)

    # Reconstruct the subscriber's (UA) public key on the P-256 curve.
    ua_public_numbers = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public_bytes)

    # Ephemeral application-server (AS) keypair, one per message.
    as_private = ec.generate_private_key(ec.SECP256R1())
    as_public_numbers = as_private.public_key().public_numbers()
    as_public_bytes = b"\x04" + as_public_numbers.x.to_bytes(32, "big") + as_public_numbers.y.to_bytes(32, "big")

    shared_secret = as_private.exchange(ec.ECDH(), ua_public_numbers)

    auth_info = b"WebPush: info\x00" + ua_public_bytes + as_public_bytes
    ikm = _hkdf(auth_secret, shared_secret, auth_info, 32)

    salt = os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)

    # A single 0x02 delimiter byte marks "last record, no padding" per RFC 8188.
    padded_plaintext = plaintext + b"\x02"

    aesgcm = AESGCM(cek)
    ciphertext = aesgcm.encrypt(nonce, padded_plaintext, None)

    record_size = (16 + len(ciphertext) + 4096).to_bytes(4, "big")  # generous single-record size
    header = (
        salt
        + (4096).to_bytes(4, "big")
        + len(as_public_bytes).to_bytes(1, "big")
        + as_public_bytes
    )
    body = header + ciphertext

    headers = {
        "Content-Type": "application/octet-stream",
        "Content-Encoding": "aes128gcm",
    }
    return body, headers


def send_push(subscription: dict, payload: dict, vapid_private_key, vapid_public_b64: str,
              vapid_subject: str = None, ttl: int = 300):
    """
    subscription: {"endpoint": ..., "p256dh": ..., "auth": ...}
    payload: JSON-serializable dict (kept small - push payloads are capped ~4KB).
    Raises PushSubscriptionGone if the browser has unsubscribed (caller should delete it).
    """
    import json as _json

    body_bytes = _json.dumps(payload).encode("utf-8")
    encrypted_body, enc_headers = encrypt_payload(body_bytes, subscription["p256dh"], subscription["auth"])

    vapid_headers = vapid_mod.build_vapid_headers(
        subscription["endpoint"], vapid_private_key, vapid_public_b64, vapid_subject
    )

    headers = {**enc_headers, **vapid_headers, "TTL": str(ttl)}

    resp = requests.post(
        subscription["endpoint"], data=encrypted_body, headers=headers,
        timeout=config.HTTP_TIMEOUT_SECONDS,
    )
    if resp.status_code in (404, 410):
        raise PushSubscriptionGone(f"subscription gone (HTTP {resp.status_code})")
    if resp.status_code not in (200, 201, 202):
        raise PushError(f"push service returned HTTP {resp.status_code}: {resp.text[:300]}")
    return True
