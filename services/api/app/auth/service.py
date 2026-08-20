from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.auth.models import CurrentActor, Role
from app.db.models import PatientIdentityLink, RoleAssignment, User

TOKEN_ISSUER = "ojcc-demo"
TOKEN_AUDIENCE = "ojcc-web"
MAX_SESSION_LIFETIME_SECONDS = 2 * 60 * 60


class ActorRepository(Protocol):
    def find_active_actor(
        self, *, organization_id: UUID, role: Role, at: datetime | None = None
    ) -> CurrentActor | None: ...


class SqlAlchemyActorRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find_active_actor(
        self, *, organization_id: UUID, role: Role, at: datetime | None = None
    ) -> CurrentActor | None:
        at = datetime.now(UTC) if at is None else at
        statement = (
            select(
                User.id,
                RoleAssignment.organization_id,
                RoleAssignment.role,
                PatientIdentityLink.patient_id,
            )
            .join(
                RoleAssignment,
                and_(
                    RoleAssignment.user_id == User.id,
                ),
            )
            .outerjoin(
                PatientIdentityLink,
                and_(
                    PatientIdentityLink.user_id == User.id,
                    PatientIdentityLink.organization_id == RoleAssignment.organization_id,
                    PatientIdentityLink.linked_at <= at,
                    PatientIdentityLink.revoked_at.is_(None)
                    | (at < PatientIdentityLink.revoked_at),
                ),
            )
            .where(
                RoleAssignment.organization_id == organization_id,
                RoleAssignment.role == role,
                RoleAssignment.granted_at <= at,
                (RoleAssignment.revoked_at.is_(None) | (at < RoleAssignment.revoked_at)),
                User.is_active.is_(True),
            )
        )
        if role == Role.SUPPORTING_ACTOR:
            statement = statement.where(PatientIdentityLink.patient_id.is_not(None))
        row = self._session.execute(statement).one_or_none()
        if row is None:
            return None
        return CurrentActor(user_id=row[0], organization_id=row[1], role=row[2], patient_id=row[3])


class DemoSessionService:
    def __init__(
        self,
        *,
        actor_repository: ActorRepository | None,
        secret: str | None,
        ttl_minutes: int,
        organization_id: UUID | None,
    ) -> None:
        if not secret:
            raise ValueError("DEMO_SESSION_SECRET must be configured")
        if not 1 <= ttl_minutes <= MAX_SESSION_LIFETIME_SECONDS // 60:
            raise ValueError("DEMO_SESSION_TTL_MINUTES must be between 1 and 120")
        self._actor_repository = actor_repository
        self._secret = secret.encode("utf-8")
        self._ttl_seconds = ttl_minutes * 60
        self._organization_id = organization_id

    def create_session(self, role: Role) -> str:
        if self._actor_repository is None or self._organization_id is None:
            raise RuntimeError("Demo session actor repository is not configured")
        actor = self._actor_repository.find_active_actor(
            organization_id=self._organization_id, role=role
        )
        if actor is None:
            raise LookupError("No active demo actor is available for this role")
        return self.create_token(actor)

    def create_token(
        self,
        actor: CurrentActor,
        *,
        issued_at: int | None = None,
        expires_at: int | None = None,
    ) -> str:
        issued_at = int(time.time()) if issued_at is None else issued_at
        expires_at = issued_at + self._ttl_seconds if expires_at is None else expires_at
        header = {"alg": "HS256", "typ": "JWT"}
        payload = {
            "aud": TOKEN_AUDIENCE,
            "exp": expires_at,
            "iat": issued_at,
            "iss": TOKEN_ISSUER,
            "jti": secrets.token_urlsafe(16),
            "nbf": issued_at,
            "org": str(actor.organization_id),
            "role": actor.role.value,
            "sub": str(actor.user_id),
        }
        if actor.patient_id is not None:
            payload["patient"] = str(actor.patient_id)
        signing_input = f"{_encode_json(header)}.{_encode_json(payload)}".encode("ascii")
        signature = hmac.new(self._secret, signing_input, hashlib.sha256).digest()
        return f"{signing_input.decode('ascii')}.{_encode_bytes(signature)}"

    def current_actor(self, token: str, *, now: int | None = None) -> CurrentActor:
        try:
            encoded_header, encoded_payload, encoded_signature = token.split(".")
            signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
            actual_signature = _decode_bytes(encoded_signature)
            expected_signature = hmac.new(self._secret, signing_input, hashlib.sha256).digest()
            if not hmac.compare_digest(actual_signature, expected_signature):
                raise ValueError
            header = _decode_json(encoded_header)
            payload = _decode_json(encoded_payload)
            if header != {"alg": "HS256", "typ": "JWT"}:
                raise ValueError
            return _actor_from_claims(payload, now=int(time.time()) if now is None else now)
        except (KeyError, TypeError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
            raise ValueError("Invalid or expired demo session") from None


def _actor_from_claims(payload: Mapping[str, object], *, now: int) -> CurrentActor:
    issued_at = _integer_claim(payload, "iat")
    expires_at = _integer_claim(payload, "exp")
    not_before = _integer_claim(payload, "nbf")
    if (
        payload.get("iss") != TOKEN_ISSUER
        or payload.get("aud") != TOKEN_AUDIENCE
        or not isinstance(payload.get("jti"), str)
        or not payload["jti"]
        or issued_at > now
        or not_before > now
        or expires_at <= now
        or expires_at <= issued_at
        or expires_at - issued_at > MAX_SESSION_LIFETIME_SECONDS
    ):
        raise ValueError
    return CurrentActor(
        user_id=UUID(_string_claim(payload, "sub")),
        organization_id=UUID(_string_claim(payload, "org")),
        role=Role(_string_claim(payload, "role")),
        patient_id=UUID(_string_claim(payload, "patient")) if "patient" in payload else None,
    )


def _integer_claim(payload: Mapping[str, object], name: str) -> int:
    value = payload[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError
    return value


def _string_claim(payload: Mapping[str, object], name: str) -> str:
    value = payload[name]
    if not isinstance(value, str):
        raise ValueError
    return value


def _encode_json(value: Mapping[str, object]) -> str:
    return _encode_bytes(json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def _decode_json(value: str) -> Mapping[str, object]:
    decoded = json.loads(_decode_bytes(value))
    if not isinstance(decoded, dict):
        raise ValueError
    return decoded


def _encode_bytes(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_bytes(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
