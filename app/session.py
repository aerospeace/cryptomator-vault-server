import secrets
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from itsdangerous import BadSignature, Signer


@dataclass
class SessionData:
    session_id: str
    created_at: float
    last_access: float
    data: dict[str, Any] = field(default_factory=dict)


class SessionStore:
    def __init__(self, secret_key: str, ttl_seconds: int) -> None:
        self.signer = Signer(secret_key)
        self.ttl_seconds = ttl_seconds
        self._lock = Lock()
        self._sessions: dict[str, SessionData] = {}

    def _now(self) -> float:
        return time.time()

    def _expired(self, session: SessionData) -> bool:
        return self._now() - session.last_access > self.ttl_seconds

    def create(self) -> SessionData:
        session_id = secrets.token_urlsafe(32)
        now = self._now()
        data = SessionData(session_id=session_id, created_at=now, last_access=now)
        with self._lock:
            self._sessions[session_id] = data
        return data

    def get(self, signed_token: str | None) -> SessionData | None:
        if not signed_token:
            return None
        try:
            session_id = self.signer.unsign(signed_token).decode("utf-8")
        except BadSignature:
            return None
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return None
            if self._expired(session):
                self._sessions.pop(session_id, None)
                return None
            session.last_access = self._now()
            return session

    def destroy(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def sign(self, session_id: str) -> str:
        return self.signer.sign(session_id).decode("utf-8")
