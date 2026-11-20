"""Dashboard authentication.

* First run: a username + password is created (scrypt hash stored in SQLite). From another LAN device the
  first-run form also requires the one-time SETUP CODE printed in the hub console (prevents a LAN stranger
  from claiming the dashboard first). Can be pre-seeded with DASHBOARD_USER / DASHBOARD_PASSWORD in .env.
* Sessions: random 256-bit token in an HttpOnly, SameSite=Strict cookie (Secure when served over HTTPS).
* Login rate limit per client IP (default 5 attempts / 5 minutes).
* Optional static API token (DASHBOARD_TOKEN) via "Authorization: Bearer <token>" or ?token=.
* Desktop app: one-time launch token passed in the URL opened by the app on localhost.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import threading
import time
from collections import defaultdict, deque

COOKIE = "an_session"
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${base64.b64encode(salt).decode()}${base64.b64encode(h).decode()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b, h_b = stored.split("$")
        if algo != "scrypt":
            return False
        h = hashlib.scrypt(pw.encode("utf-8"), salt=base64.b64decode(salt_b), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(h, base64.b64decode(h_b))
    except (ValueError, TypeError):
        return False


def password_problems(username: str, pw: str) -> str | None:
    if not username or len(username) < 3 or len(username) > 32 or not username.replace("_", "").replace("-", "").isalnum():
        return "username: 3-32 letters/digits/-/_"
    if len(pw) < 8:
        return "password: at least 8 characters"
    if pw.lower() == username.lower():
        return "password must differ from the username"
    return None


class RateLimiter:
    def __init__(self, max_attempts: int = 5, window_s: float = 300.0, clock=time.time):
        self.max = max_attempts
        self.window = window_s
        self.clock = clock
        self.hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def blocked(self, key: str) -> float:
        """Seconds until the key may try again (0 = allowed)."""
        now = self.clock()
        with self._lock:
            dq = self.hits[key]
            while dq and now - dq[0] > self.window:
                dq.popleft()
            if len(dq) >= self.max:
                return self.window - (now - dq[0])
        return 0.0

    def hit(self, key: str) -> None:
        with self._lock:
            self.hits[key].append(self.clock())

    def reset(self, key: str) -> None:
        with self._lock:
            self.hits.pop(key, None)


class Auth:
    def __init__(self, storage, session_hours: float = 12, max_attempts: int = 5, window_s: float = 300,
                 clock=time.time):
        self.storage = storage
        self.ttl = session_hours * 3600
        self.clock = clock
        self.sessions: dict[str, tuple[str, float]] = {}
        self.limiter = RateLimiter(max_attempts, window_s, clock)
        self.api_token = os.environ.get("DASHBOARD_TOKEN") or None
        self.setup_code = secrets.token_hex(3).upper()
        self.launch_tokens: dict[str, float] = {}
        self._lock = threading.Lock()
        u, p = os.environ.get("DASHBOARD_USER"), os.environ.get("DASHBOARD_PASSWORD")
        if u and p and self.storage.user_count() == 0 and not password_problems(u, p):
            self.storage.create_user(u, hash_password(p))

    @property
    def needs_setup(self) -> bool:
        return self.storage.user_count() == 0

    def create_first_user(self, username: str, pw: str, setup_code: str | None, is_local: bool) -> str | None:
        if not self.needs_setup:
            return "already configured"
        if not is_local and (setup_code or "").strip().upper() != self.setup_code:
            return "setup code required (shown in the hub console window)"
        prob = password_problems(username, pw)
        if prob:
            return prob
        self.storage.create_user(username, hash_password(pw))
        return None

    def login(self, username: str, pw: str, ip: str) -> tuple[str | None, str]:
        wait = self.limiter.blocked(ip)
        if wait > 0:
            return None, f"too many attempts - try again in {int(wait) + 1}s"
        u = self.storage.get_user(username or "")
        if not u or not verify_password(pw or "", u["pw_hash"]):
            self.limiter.hit(ip)
            return None, "invalid username or password"
        self.limiter.reset(ip)
        return self.new_session(username), "ok"

    def new_session(self, username: str) -> str:
        tok = secrets.token_urlsafe(32)
        with self._lock:
            self.sessions[tok] = (username, self.clock() + self.ttl)
        return tok

    def user_for(self, token: str | None) -> str | None:
        if not token:
            return None
        with self._lock:
            rec = self.sessions.get(token)
            if not rec:
                return None
            if rec[1] < self.clock():
                del self.sessions[token]
                return None
            return rec[0]

    def logout(self, token: str | None) -> None:
        with self._lock:
            self.sessions.pop(token or "", None)

    def check_api_token(self, presented: str | None) -> bool:
        return bool(self.api_token and presented and hmac.compare_digest(self.api_token, presented))

    def make_launch_token(self) -> str:
        t = secrets.token_urlsafe(24)
        self.launch_tokens[t] = self.clock() + 120
        return t

    def use_launch_token(self, t: str) -> bool:
        exp = self.launch_tokens.pop(t or "", None)
        return bool(exp and exp >= self.clock())
