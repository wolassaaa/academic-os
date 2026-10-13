from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timezone

from .db import dumps


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("Password must contain at least 8 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password, encoded):
    try:
        scheme, salt, expected = encoded.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(digest, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class SecretStore:
    """Credential Manager handles, never inline secrets in configuration or exports."""
    service = "academic-os-v2"

    def get(self, name):
        import keyring
        value = keyring.get_password(self.service, name)
        if not value:
            raise RuntimeError(f"credential_missing:{name}")
        return value

    def set(self, name, value):
        import keyring
        keyring.set_password(self.service, name, value)

    def delete(self, name):
        import keyring
        try:
            keyring.delete_password(self.service, name)
        except keyring.errors.PasswordDeleteError:
            pass


class Auth:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings

    def set_password(self, username, password):
        if not username or len(username) > 100:
            raise ValueError("Invalid username")
        encoded = hash_password(password)
        with self.db.transaction() as db:
            db.execute("INSERT OR REPLACE INTO users VALUES(?,?)", (username, encoded))
            db.execute("DELETE FROM sessions WHERE username=?", (username,))

    def reset_password(self, username, password):
        """Trusted host-local recovery only; never exposed through HTTP or MCP."""
        encoded = hash_password(password)
        with self.db.transaction() as db:
            if username is None:
                users = db.execute("SELECT username FROM users").fetchall()
                if len(users) != 1:
                    raise ValueError("Specify --username for an existing account")
                username = users[0][0]
            result = db.execute("UPDATE users SET password_hash=? WHERE username=?", (encoded, username))
            if result.rowcount != 1:
                raise ValueError("Account does not exist")
            db.execute("DELETE FROM sessions WHERE username=?", (username,))
            db.execute("DELETE FROM login_attempts")
            db.execute("INSERT INTO audit(actor,action,entity_id,document,created_at) VALUES(?,?,?,?,?)", ("owner:cli", "auth.password_reset", username, dumps({"sessions_revoked": True}), datetime.now(timezone.utc).isoformat()))
        return username

    def login(self, username, password, address, remote):
        now = time.time()
        key = hashlib.sha256(f"{address}:{username}".encode()).hexdigest()
        address_key = "address:" + hashlib.sha256(address.encode()).hexdigest()
        with self.db.transaction() as db:
            db.execute("DELETE FROM login_attempts WHERE window_start<?", (now - 900,))
            address_attempt = db.execute("SELECT * FROM login_attempts WHERE key=?", (address_key,)).fetchone()
            if address_attempt and address_attempt["failures"] >= 30:
                raise PermissionError("Too many attempts; try again in 15 minutes")
            attempt = db.execute("SELECT * FROM login_attempts WHERE key=?", (key,)).fetchone()
            if attempt and now - attempt["window_start"] < 900 and attempt["failures"] >= 5:
                raise PermissionError("Too many attempts; try again in 15 minutes")
            user = db.execute("SELECT password_hash FROM users WHERE username=?", (username,)).fetchone()
            # Consume equivalent KDF work for an unknown username.
            valid = verify_password(password, user[0] if user else hash_password("invalid-user-password"))
            if not user or not valid:
                count = attempt["failures"] + 1 if attempt and now - attempt["window_start"] < 900 else 1
                started = attempt["window_start"] if count > 1 else now
                db.execute("INSERT OR REPLACE INTO login_attempts VALUES(?,?,?)", (key, count, started))
                db.execute("INSERT INTO login_attempts VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET failures=failures+1", (address_key, now))
                result = None
            else:
                db.execute("DELETE FROM login_attempts WHERE key=?", (key,))
                token = secrets.token_urlsafe(32)
                db.execute("DELETE FROM sessions WHERE expires_at<?", (now,))
                db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (token_hash(token), username, now + self.settings.session_hours * 3600, int(remote)))
                result = token
        if result is None:
            raise PermissionError("Invalid credentials")
        return result

    def session(self, token, remote):
        if not token:
            return None
        with self.db.connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE token_hash=? AND expires_at>? AND remote=?", (token_hash(token), time.time(), int(remote))).fetchone()
        return row["username"] if row else None

    def logout(self, token):
        with self.db.transaction() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(token or ""),))

    def issue_token(self, name, scopes):
        allowed = {"read", "draft", "sync", "confirm", "notify"}
        if not scopes or not set(scopes) <= allowed:
            raise ValueError("Unknown token scopes")
        token = secrets.token_urlsafe(40)
        with self.db.transaction() as db:
            db.execute("INSERT INTO tokens VALUES(?,?,?,?)", (token_hash(token), name, dumps(scopes), datetime.now(timezone.utc).isoformat()))
        return token

    def token_scopes(self, token):
        with self.db.connect() as db:
            row = db.execute("SELECT scopes FROM tokens WHERE token_hash=?", (token_hash(token),)).fetchone()
        return set(json.loads(row[0])) if row else set()
