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

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.authority import (
    AuthorityDatabaseUnavailableError,
    authority_from_row,
    build_authority_statement,
)
from app.auth.demo_actors import DemoActorSelection
from app.auth.models import CurrentActor, ResolvedAuthority, Role, VerifiedDemoSession

TOKEN_ISSUER = "ojcc-demo"
TOKEN_AUDIENCE = "ojcc-web"
MAX_SESSION_LIFETIME_SECONDS = 2 * 60 * 60


class ActorRepository(Protocol):
    def resolve_authority(
        self,
        *,
        organization_id: UUID,
        user_id: UUID,
        role: Role,
        at: datetime | None = None,
    ) -> ResolvedAuthority | None: ...

class SqlAlchemyActorRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve_authority(
        self,
        *,
        organization_id: UUID,
        user_id: UUID,
        role: Role,
        at: datetime | None = None,
    ) -> ResolvedAuthority | None:
        checked_at = datetime.now(UTC) if at is None else at
        try:
            row = self._session.execute(
                build_authority_statement(
                    organization_id=organization_id,
                    user_id=user_id,
                    role=role,
                    at=checked_at,
                )
            ).one_or_none()
        except SQLAlchemyError:
            raise AuthorityDatabaseUnavailableError() from None
        return authority_from_row(
            row,
            organization_id=organization_id,
            user_id=user_id,
            role=role,
        )

class DemoSessionService:
    def __init__(
        self,
        *,
        actor_repository: ActorRepository | None,
        secret: str | None,
        ttl_minutes: int,
        organization_id: UUID | None,
        demo_actors: Mapping[Role, DemoActorSelection] | None = None,
    ) -> None:
        if not secret:
            raise ValueError("DEMO_SESSION_SECRET must be configured")
        if not 1 <= ttl_minutes <= MAX_SESSION_LIFETIME_SECONDS // 60:
            raise ValueError("DEMO_SESSION_TTL_MINUTES must be between 1 and 120")
        self._actor_repository = actor_repository
        self._secret = secret.encode("utf-8")
        self._ttl_seconds = ttl_minutes * 60
        self._organization_id = organization_id
        self._demo_actors = dict(demo_actors) if demo_actors is not None else None

    def create_session(self, role: Role) -> str:
        if (
            self._actor_repository is None
            or self._organization_id is None
            or self._demo_actors is None
        ):
            raise RuntimeError("Demo session actor repository is not configured")
        selection = self._demo_actors.get(role)
        if selection is None:
            raise LookupError("No active demo actor is available for this role")
        authority = self._actor_repository.resolve_authority(
            organization_id=self._organization_id,
            user_id=selection.user_id,
            role=role,
        )
        if authority is None or authority.actor.patient_id != selection.patient_id:
            raise LookupError("No active demo actor is available for this role")
        return self.create_token(authority)

    def is_configured_actor(self, actor: CurrentActor) -> bool:
        if self._organization_id is None or self._demo_actors is None:
            return False
        selection = self._demo_actors.get(actor.role)
        return (
            selection is not None
            and actor.organization_id == self._organization_id
            and actor.user_id == selection.user_id
            and actor.patient_id == selection.patient_id
        )

    def create_token(
        self,
        authority: ResolvedAuthority,
        *,
        issued_at: int | None = None,
        expires_at: int | None = None,
    ) -> str:
        actor = authority.actor
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
            "ra": str(authority.role_assignment_id),
            "role": actor.role.value,
            "sub": str(actor.user_id),
            "ver": 2,
        }
        if actor.role == Role.SUPPORTING_ACTOR:
            if actor.patient_id is None or authority.patient_identity_link_id is None:
                raise ValueError("Supporting actor authority requires patient provenance")
            payload["patient"] = str(actor.patient_id)
            payload["pil"] = str(authority.patient_identity_link_id)
        elif actor.patient_id is not None or authority.patient_identity_link_id is not None:
            raise ValueError("Staff authority cannot include patient provenance")
        signing_input = f"{_encode_json(header)}.{_encode_json(payload)}".encode("ascii")
        signature = hmac.new(self._secret, signing_input, hashlib.sha256).digest()
        return f"{signing_input.decode('ascii')}.{_encode_bytes(signature)}"

    def verify_session(self, token: str, *, now: int | None = None) -> VerifiedDemoSession:
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
            version = payload.get("ver")
            if type(version) is not int or version != 2:
                raise ValueError
            actor = _actor_from_claims(payload, now=int(time.time()) if now is None else now)
            patient_identity_link_id = (
                _uuid_claim(payload, "pil")
                if actor.role == Role.SUPPORTING_ACTOR
                else None
            )
            return VerifiedDemoSession(
                authority=ResolvedAuthority(
                    actor=actor,
                    role_assignment_id=_uuid_claim(payload, "ra"),
                    patient_identity_link_id=patient_identity_link_id,
                )
            )
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
    role = Role(_string_claim(payload, "role"))
    if role == Role.SUPPORTING_ACTOR:
        patient_id = _uuid_claim(payload, "patient")
        _uuid_claim(payload, "pil")
    else:
        if "patient" in payload or "pil" in payload:
            raise ValueError
        patient_id = None
    return CurrentActor(
        user_id=_uuid_claim(payload, "sub"),
        organization_id=_uuid_claim(payload, "org"),
        role=role,
        patient_id=patient_id,
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


def _uuid_claim(payload: Mapping[str, object], name: str) -> UUID:
    return UUID(_string_claim(payload, name))


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
