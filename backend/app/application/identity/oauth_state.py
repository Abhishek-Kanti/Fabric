from abc import ABC, abstractmethod
import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import threading
import uuid

from app.domain.identity.exceptions import InvalidOAuthStateException


@dataclass(frozen=True)
class OAuthState:
    """Ephemeral record representing an active interactive login attempt."""

    state_id: str
    workspace_id: uuid.UUID
    code_verifier: str
    nonce: str
    redirect_url: str
    created_at: datetime
    expires_at: datetime


class OAuthStateStore(ABC):
    """Abstract port for persisting and atomically consuming ephemeral OAuth login states."""

    @abstractmethod
    def save_state(self, state: OAuthState) -> None:
        """Store an ephemeral OAuth state."""
        ...

    @abstractmethod
    def consume_state(self, state_id: str) -> OAuthState:
        """Atomically retrieve and consume an OAuth state, preventing replay attacks.
        
        Raises:
            InvalidOAuthStateException: If state does not exist, has expired, or was already consumed.
        """
        ...


class MemoryOAuthStateStore(OAuthStateStore):
    """Thread-safe, in-process memory state store for single-worker deployments and tests.
    
    Architectural Deployment Invariant:
    This store is bounded to a single Python process / single worker. Multi-worker uvicorn
    or multi-replica deployments must provide a shared distributed store (e.g. Redis).
    """

    def __init__(self) -> None:
        self._states: dict[str, OAuthState] = {}
        self._lock = threading.Lock()

    def save_state(self, state: OAuthState) -> None:
        now = datetime.now(timezone.utc)
        with self._lock:
            self._cleanup_expired_locked(now)
            self._states[state.state_id] = state

    def consume_state(self, state_id: str) -> OAuthState:
        now = datetime.now(timezone.utc)
        with self._lock:
            state = self._states.pop(state_id, None)

        if state is None:
            raise InvalidOAuthStateException(
                "OAuth state is invalid, expired, or has already been consumed (replay rejected)"
            )

        if now > state.expires_at:
            raise InvalidOAuthStateException("OAuth state has expired")

        return state

    def _cleanup_expired_locked(self, now: datetime) -> None:
        expired = [s_id for s_id, s in self._states.items() if now > s.expires_at]
        for s_id in expired:
            del self._states[s_id]


class OAuthStateManager:
    """State manager enforcing one-time state consumption, PKCE pairing, and TTL expiry."""

    def __init__(
        self,
        default_ttl_seconds: int = 600,
        store: OAuthStateStore | None = None,
    ) -> None:
        self._default_ttl_seconds = default_ttl_seconds
        self._store = store or MemoryOAuthStateStore()

    def generate_state(
        self,
        workspace_id: uuid.UUID,
        redirect_url: str | None = None,
    ) -> tuple[OAuthState, str]:
        """Generate PKCE challenge, OIDC nonce, and register an ephemeral OAuth state.
        
        Returns:
            Tuple of (OAuthState, code_challenge).
        """
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=self._default_ttl_seconds)

        code_verifier = secrets.token_urlsafe(64)
        hashed = hashlib.sha256(code_verifier.encode("ascii")).digest()
        code_challenge = base64.urlsafe_b64encode(hashed).decode("ascii").rstrip("=")

        nonce = secrets.token_urlsafe(32)
        state_id = secrets.token_urlsafe(32)

        safe_redirect_url = self.validate_safe_redirect_url(redirect_url)

        state = OAuthState(
            state_id=state_id,
            workspace_id=workspace_id,
            code_verifier=code_verifier,
            nonce=nonce,
            redirect_url=safe_redirect_url,
            created_at=now,
            expires_at=expires_at,
        )

        self._store.save_state(state)
        return state, code_challenge

    def consume_state(self, state_id: str) -> OAuthState:
        """Atomically retrieve and consume an OAuth state, preventing replay attacks.
        
        Raises:
            InvalidOAuthStateException: If state does not exist, has expired, or was already consumed.
        """
        return self._store.consume_state(state_id)

    @staticmethod
    def validate_safe_redirect_url(url: str | None) -> str:
        """Ensure redirect URL is a safe local relative path to prevent open redirect vulnerabilities."""
        if not url:
            return "/"
        # Must start with a single / and not // (protocol-relative) and not contain \
        if url.startswith("/") and not url.startswith("//") and "\\" not in url:
            return url
        return "/"
