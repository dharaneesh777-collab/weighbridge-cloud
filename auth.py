import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, Optional

SESSION_COOKIE_NAME = "wb_cloud_session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days
PBKDF2_ITERATIONS = 210_000


def _get_session_secret() -> bytes:
    secret = (os.getenv("SESSION_SECRET") or os.getenv("ADMIN_KEY") or os.getenv("EDGE_KEY") or "wb_cloud_dev_secret").strip()
    return secret.encode("utf-8")


def hash_password(password: str) -> str:
    """
    Hashes password with random 16-byte salt using PBKDF2-HMAC-SHA256 (210,000 iterations).
    Format: pbkdf2_sha256$<iterations>$<salt_hex>$<digest_hex>
    """
    if not password or len(password) < 4:
        raise ValueError("Password must be at least 4 characters.")
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Constant-time verification against PBKDF2-HMAC-SHA256 stored hash."""
    if not password or not stored_hash:
        return False
    try:
        algo, iter_str, salt_hex, digest_hex = stored_hash.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iter_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def create_session_token(username: str, role: str, full_name: str = "") -> str:
    payload = {
        "u": username,
        "r": role,
        "n": full_name or username,
        "exp": int(time.time()) + SESSION_TTL_SECONDS,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    b64 = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    sig = hmac.new(_get_session_secret(), b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{b64}.{sig}"


def verify_session_token(token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token or "." not in token:
        return None
    try:
        b64, sig = token.split(".", 1)
        expected_sig = hmac.new(_get_session_secret(), b64.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        padded = b64 + "=" * (-len(b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return {
            "username": payload["u"],
            "role": payload["r"],
            "full_name": payload.get("n", payload["u"]),
        }
    except Exception:
        return None
