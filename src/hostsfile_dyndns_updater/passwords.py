"""Password hashing (PBKDF2-HMAC-SHA256, stdlib only) and verification."""

import base64
import hashlib
import hmac
import secrets

SCHEME = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 600_000
MIN_ITERATIONS = 100_000


def hash_password(password: str, iterations: int = DEFAULT_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return "$".join(
        (
            SCHEME,
            str(iterations),
            base64.b64encode(salt).decode(),
            base64.b64encode(digest).decode(),
        )
    )


def parse_hash(encoded: str) -> tuple[int, bytes, bytes]:
    """Return (iterations, salt, digest) or raise ValueError."""
    try:
        scheme, iterations, salt, digest = encoded.split("$")
        if scheme != SCHEME:
            raise ValueError("unsupported hash scheme")
        parsed = (int(iterations), base64.b64decode(salt, validate=True),
                  base64.b64decode(digest, validate=True))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"malformed password hash: {exc}") from exc
    if parsed[0] < MIN_ITERATIONS:
        raise ValueError(f"iteration count below {MIN_ITERATIONS}")
    return parsed


def verify_password(password: str, encoded: str) -> bool:
    iterations, salt, expected = parse_hash(encoded)
    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt, iterations, dklen=len(expected)
    )
    return hmac.compare_digest(actual, expected)
