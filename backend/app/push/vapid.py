"""
VAPID (RFC 8292) key management + JWT signing.

VAPID lets our server identify itself to push services (Chrome's FCM,
Firefox's autopush, Apple's push service, ...) without a separate API key
per provider - we just sign a short-lived JWT with our own EC keypair and
send the matching public key alongside it.
"""
import base64
import json
import time
from pathlib import Path

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

from .. import config


def _b64url_encode(raw_bytes: bytes) -> str:
    return base64.urlsafe_b64encode(raw_bytes).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def _public_key_raw_bytes(private_key) -> bytes:
    numbers = private_key.public_key().public_numbers()
    return b"\x04" + numbers.x.to_bytes(32, "big") + numbers.y.to_bytes(32, "big")


def generate_keypair():
    private_key = ec.generate_private_key(ec.SECP256R1())
    return private_key


def save_keys(private_key, path: str = None):
    path = path or config.VAPID_KEYS_PATH
    private_value = private_key.private_numbers().private_value
    data = {
        "private_key_b64": _b64url_encode(private_value.to_bytes(32, "big")),
        "public_key_b64": _b64url_encode(_public_key_raw_bytes(private_key)),
    }
    Path(path).write_text(json.dumps(data))
    return data


def load_or_create_keys(path: str = None):
    """Returns (private_key_obj, public_key_b64url). Creates + persists a new keypair on first run."""
    path = path or config.VAPID_KEYS_PATH
    p = Path(path)
    if p.exists():
        data = json.loads(p.read_text())
        private_value = int.from_bytes(_b64url_decode(data["private_key_b64"]), "big")
        private_key = ec.derive_private_key(private_value, ec.SECP256R1())
        return private_key, data["public_key_b64"]

    private_key = generate_keypair()
    data = save_keys(private_key, path)
    return private_key, data["public_key_b64"]


def build_vapid_headers(endpoint: str, private_key, public_key_b64url: str, subject: str = None) -> dict:
    """
    endpoint: the push subscription's endpoint URL (its origin becomes the JWT 'aud').
    Returns headers to attach to the POST request to that endpoint.
    """
    from urllib.parse import urlparse

    parsed = urlparse(endpoint)
    aud = f"{parsed.scheme}://{parsed.netloc}"
    now = int(time.time())
    claims = {
        "aud": aud,
        "exp": now + 12 * 3600,  # must be < 24h per RFC 8292
        "sub": subject or config.VAPID_SUBJECT,
    }
    token = jwt.encode(claims, private_key, algorithm="ES256")
    return {
        "Authorization": f"vapid t={token}, k={public_key_b64url}",
    }
