import time
from collections import deque
from threading import Lock


class RateLimiter:
    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._lock = Lock()
        self._attempts: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            attempts = self._attempts.setdefault(key, deque())
            while attempts and now - attempts[0] > self.window_seconds:
                attempts.popleft()
            if len(attempts) >= self.max_attempts:
                return False
            attempts.append(now)
            return True
