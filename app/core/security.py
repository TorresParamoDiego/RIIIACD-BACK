"""Contraseñas, tokens y límite de solicitudes."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


# Hash de una contraseña inexistente: iguala el tiempo de respuesta cuando el usuario no existe.
DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def new_token() -> str:
    """Token de alta entropía (256 bits) para enlaces y sesiones."""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    """Se guarda solo el SHA-256: con 256 bits de entropía no hace falta un hash lento."""
    return hashlib.sha256(token.encode()).hexdigest()


def same_token(token: str, expected_hash: str) -> bool:
    return hmac.compare_digest(token_hash(token), expected_hash)


class RateLimiter:
    """Ventana deslizante en memoria del proceso.

    Suficiente para un solo proceso. Con varios procesos o servidores, cada uno
    lleva su cuenta; en ese caso conviene moverlo a Redis o al proxy inverso.
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


rate_limiter = RateLimiter()
