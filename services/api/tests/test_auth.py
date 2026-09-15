import asyncio
import base64
import hashlib
import hmac
import json
from datetime import datetime
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException, Request

from app.auth.demo_actors import DemoActorSelection
from app.auth.dependencies import current_actor, require_role
from app.auth.models import CurrentActor, ResolvedAuthority, Role, VerifiedDemoSession
from app.auth.service import ActorRepository, DemoSessionService
from app.config import Settings
from app.main import app

TEST_SESSION_SECRET = "test-only-signing-secret"


def _actor(role: Role) -> CurrentActor:
    return CurrentActor(user_id=uuid4(), organization_id=uuid4(), role=role)


def _authority(role: Role) -> ResolvedAuthority:
    base_actor = _actor(role)
    actor = (
        CurrentActor(
            user_id=base_actor.user_id,
            organization_id=base_actor.organization_id,
            role=role,
            patient_id=uuid4(),
        )
        if role == Role.SUPPORTING_ACTOR
        else base_actor
    )
    return ResolvedAuthority(
        actor=actor,
        role_assignment_id=uuid4(),
        patient_identity_link_id=(uuid4() if role == Role.SUPPORTING_ACTOR else None),
    )


def _authority_for_actor(actor: CurrentActor) -> ResolvedAuthority:
    return ResolvedAuthority(
        actor=actor,
        role_assignment_id=uuid4(),
        patient_identity_link_id=(
            uuid4() if actor.role == Role.SUPPORTING_ACTOR else None
        ),
    )


class StaticActorRepository(ActorRepository):
    def __init__(self, actors: dict[Role, CurrentActor]) -> None:
        self._actors = actors

    def resolve_authority(
        self,
        *,
        organization_id: UUID,
        user_id: UUID,
        role: Role,
        at: datetime | None = None,
    ) -> ResolvedAuthority | None:
        del at
        actor = self._actors.get(role)
        if (
            actor is None
            or actor.organization_id != organization_id
            or actor.user_id != user_id
        ):
            return None
        return ResolvedAuthority(actor=actor, role_assignment_id=uuid4())


def _resign_token(
    token: str,
    mutate_claims: dict[str, object],
    *,
    remove_claims: tuple[str, ...] = (),
) -> str:
    header, encoded_payload, _ = token.split(".")
    payload = json.loads(_decode_base64url(encoded_payload))
    payload.update(mutate_claims)
    for claim in remove_claims:
        payload.pop(claim, None)
    encoded_payload = _encode_base64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header}.{encoded_payload}".encode()
    signature = hmac.new(TEST_SESSION_SECRET.encode(), signing_input, hashlib.sha256).digest()
    return f"{header}.{encoded_payload}.{_encode_base64url(signature)}"


def _decode_base64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _encode_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def test_patient_facing_supporting_actor_cannot_use_navigator_permission() -> None:
    """This fails if require_role accidentally permits every authenticated actor."""
    dependency = require_role(Role.NAVIGATOR)

    with pytest.raises(HTTPException) as exc_info:
        dependency(_actor(Role.SUPPORTING_ACTOR))

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Role not permitted"


def test_http_valid_but_wrong_role_remains_forbidden() -> None:
    app.dependency_overrides[current_actor] = lambda: _actor(Role.ADMINISTRATOR)

    async def load_queue() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.get("/v1/navigator/queue")

    try:
        response = asyncio.run(load_queue())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.json() == {"detail": "Role not permitted"}


def test_http_unknown_demo_role_remains_unprocessable() -> None:
    from app.api import demo_sessions

    actor = _actor(Role.NAVIGATOR)
    session_service = DemoSessionService(
        actor_repository=StaticActorRepository({Role.NAVIGATOR: actor}),
        secret=TEST_SESSION_SECRET,
        ttl_minutes=30,
        organization_id=actor.organization_id,
        demo_actors={Role.NAVIGATOR: DemoActorSelection(user_id=actor.user_id)},
    )
    app.dependency_overrides[demo_sessions.get_demo_session_service] = (
        lambda: session_service
    )

    async def issue_session() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.post("/v1/demo/session/unknown-role")

    try:
        response = asyncio.run(issue_session())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


@pytest.mark.parametrize(
    "version",
    [pytest.param("missing", id="missing"), "legacy", 1, 3, "2", True, None, [], {}],
)
def test_demo_session_rejects_invalid_token_version(version: object) -> None:
    authority = _authority(Role.NAVIGATOR)
    service = DemoSessionService(
        actor_repository=StaticActorRepository({Role.NAVIGATOR: authority.actor}),
        secret=TEST_SESSION_SECRET,
        ttl_minutes=30,
        organization_id=authority.actor.organization_id,
    )
    token = service.create_token(authority, issued_at=1_000, expires_at=2_000)
    mutated = (
        _resign_token(token, {}, remove_claims=("ver",))
        if version == "missing"
        else _resign_token(token, {"ver": version})
    )

    with pytest.raises(ValueError, match="Invalid or expired demo session"):
        service.verify_session(mutated, now=1_000)


def test_demo_session_issues_exact_integer_token_version_two() -> None:
    authority = _authority(Role.NAVIGATOR)
    service = DemoSessionService(
        actor_repository=StaticActorRepository({Role.NAVIGATOR: authority.actor}),
        secret=TEST_SESSION_SECRET,
        ttl_minutes=30,
        organization_id=authority.actor.organization_id,
    )
    token = service.create_token(authority, issued_at=1_000, expires_at=2_000)

    payload = json.loads(_decode_base64url(token.split(".")[1]))
    assert type(payload["ver"]) is int
    assert payload["ver"] == 2


@pytest.mark.parametrize(
    "role_assignment_claim",
    [pytest.param("missing", id="missing"), "", "not-a-uuid", 7, True, None, [], {}],
)
def test_demo_session_rejects_invalid_role_assignment_claim(
    role_assignment_claim: object,
) -> None:
    authority = _authority(Role.NAVIGATOR)
    service = DemoSessionService(
        actor_repository=StaticActorRepository({Role.NAVIGATOR: authority.actor}),
        secret=TEST_SESSION_SECRET,
        ttl_minutes=30,
        organization_id=authority.actor.organization_id,
    )
    token = service.create_token(authority, issued_at=1_000, expires_at=2_000)
    mutated = (
        _resign_token(token, {}, remove_claims=("ra",))
        if role_assignment_claim == "missing"
        else _resign_token(token, {"ra": role_assignment_claim})
    )

    with pytest.raises(ValueError, match="Invalid or expired demo session"):
        service.verify_session(mutated, now=1_000)


def test_demo_session_verifies_role_assignment_provenance() -> None:
    authority = _authority(Role.NAVIGATOR)
    service = DemoSessionService(
        actor_repository=StaticActorRepository({Role.NAVIGATOR: authority.actor}),
        secret=TEST_SESSION_SECRET,
        ttl_minutes=30,
        organization_id=authority.actor.organization_id,
    )
    token = service.create_token(authority, issued_at=1_000, expires_at=2_000)

    assert service.verify_session(token, now=1_000) == VerifiedDemoSession(
        authority=authority
    )


@pytest.mark.parametrize(
    ("claim_update", "remove_claims"),
    [
        ({}, ("patient",)),
        ({}, ("pil",)),
        ({}, ("patient", "pil")),
        ({"patient": ""}, ()),
        ({"patient": "not-a-uuid"}, ()),
        ({"patient": 7}, ()),
        ({"patient": True}, ()),
        ({"patient": None}, ()),
        ({"patient": []}, ()),
        ({"patient": {}}, ()),
        ({"pil": ""}, ()),
        ({"pil": "not-a-uuid"}, ()),
        ({"pil": 7}, ()),
        ({"pil": True}, ()),
        ({"pil": None}, ()),
        ({"pil": []}, ()),
        ({"pil": {}}, ()),
    ],
    ids=[
        "missing-patient",
        "missing-pil",
        "missing-both",
        "empty-patient",
        "malformed-patient",
        "integer-patient",
        "boolean-patient",
        "null-patient",
        "list-patient",
        "object-patient",
        "empty-pil",
        "malformed-pil",
        "integer-pil",
        "boolean-pil",
        "null-pil",
        "list-pil",
        "object-pil",
    ],
)
def test_supporting_actor_rejects_invalid_patient_claim_shape(
    claim_update: dict[str, object],
    remove_claims: tuple[str, ...],
) -> None:
    authority = _authority(Role.SUPPORTING_ACTOR)
    service = DemoSessionService(
        actor_repository=StaticActorRepository({Role.SUPPORTING_ACTOR: authority.actor}),
        secret=TEST_SESSION_SECRET,
        ttl_minutes=30,
        organization_id=authority.actor.organization_id,
    )
    token = service.create_token(authority, issued_at=1_000, expires_at=2_000)
    assert authority.patient_identity_link_id is not None
    token_with_pil = _resign_token(
        token,
        {"pil": str(authority.patient_identity_link_id)},
    )

    with pytest.raises(ValueError, match="Invalid or expired demo session"):
        service.verify_session(
            _resign_token(
                token_with_pil,
                claim_update,
                remove_claims=remove_claims,
            ),
            now=1_000,
        )


@pytest.mark.parametrize(
    "claim_update",
    [
        {"patient": str(uuid4())},
        {"pil": str(uuid4())},
        {"patient": str(uuid4()), "pil": str(uuid4())},
    ],
    ids=["patient-only", "pil-only", "patient-and-pil"],
)
@pytest.mark.parametrize("role", [Role.NAVIGATOR, Role.ADMINISTRATOR])
def test_staff_actor_rejects_patient_claims(
    role: Role,
    claim_update: dict[str, object],
) -> None:
    authority = _authority(role)
    service = DemoSessionService(
        actor_repository=StaticActorRepository({role: authority.actor}),
        secret=TEST_SESSION_SECRET,
        ttl_minutes=30,
        organization_id=authority.actor.organization_id,
    )
    token = service.create_token(authority, issued_at=1_000, expires_at=2_000)

    with pytest.raises(ValueError, match="Invalid or expired demo session"):
        service.verify_session(_resign_token(token, claim_update), now=1_000)


@pytest.mark.parametrize(
    "role",
    [Role.NAVIGATOR, Role.ADMINISTRATOR, Role.SUPPORTING_ACTOR],
)
def test_demo_session_verifies_role_specific_patient_provenance(role: Role) -> None:
    authority = _authority(role)
    service = DemoSessionService(
        actor_repository=StaticActorRepository({role: authority.actor}),
        secret=TEST_SESSION_SECRET,
        ttl_minutes=30,
        organization_id=authority.actor.organization_id,
    )
    token = service.create_token(authority, issued_at=1_000, expires_at=2_000)

    assert service.verify_session(token, now=1_000) == VerifiedDemoSession(
        authority=authority
    )


def test_demo_sessions_are_signed_and_tamper_evident() -> None:
    """This fails if the token signature is omitted or its comparison is not enforced."""
    actor = _actor(Role.NAVIGATOR)
    service = DemoSessionService(
        actor_repository=StaticActorRepository({Role.NAVIGATOR: actor}),
        secret="test-only-signing-secret",
        ttl_minutes=30,
        organization_id=actor.organization_id,
    )

    authority = _authority_for_actor(actor)
    token = service.create_token(authority)
    tampered_token = f"{'A' if token[0] != 'A' else 'B'}{token[1:]}"

    assert service.verify_session(token).authority == authority
    with pytest.raises(ValueError, match="Invalid or expired demo session"):
        service.verify_session(tampered_token)


def test_demo_sessions_reject_expired_or_overlong_tokens() -> None:
    """This fails if token expiry or the two-hour maximum lifetime is not checked."""
    actor = _actor(Role.NAVIGATOR)
    service = DemoSessionService(
        actor_repository=StaticActorRepository({Role.NAVIGATOR: actor}),
        secret="test-only-signing-secret",
        ttl_minutes=120,
        organization_id=actor.organization_id,
    )

    authority = _authority_for_actor(actor)
    expired_token = service.create_token(authority, issued_at=1_000, expires_at=1_001)
    overlong_token = service.create_token(authority, issued_at=1_000, expires_at=8_201)

    with pytest.raises(ValueError, match="Invalid or expired demo session"):
        service.verify_session(expired_token, now=1_002)
    with pytest.raises(ValueError, match="Invalid or expired demo session"):
        service.verify_session(overlong_token, now=1_001)


@pytest.mark.parametrize("ttl_minutes", [1, 120])
def test_demo_session_accepts_ttl_boundaries_and_expires_at_exact_boundary(
    ttl_minutes: int,
) -> None:
    authority = _authority(Role.NAVIGATOR)
    service = DemoSessionService(
        actor_repository=StaticActorRepository({Role.NAVIGATOR: authority.actor}),
        secret=TEST_SESSION_SECRET,
        ttl_minutes=ttl_minutes,
        organization_id=authority.actor.organization_id,
    )
    issued_at = 1_000
    expires_at = issued_at + ttl_minutes * 60
    token = service.create_token(authority, issued_at=issued_at)
    payload = json.loads(_decode_base64url(token.split(".")[1]))

    assert payload["exp"] == expires_at
    assert service.verify_session(token, now=expires_at - 1).authority == authority
    with pytest.raises(ValueError, match="Invalid or expired demo session"):
        service.verify_session(token, now=expires_at)


@pytest.mark.parametrize(
    "claim_update",
    [
        {"iss": "wrong-issuer"},
        {"aud": "wrong-audience"},
        {"jti": ""},
        {"nbf": 1_001},
    ],
)
def test_demo_sessions_reject_invalid_identity_claims(claim_update: dict[str, object]) -> None:
    """This fails if identity claims are not independently validated after signing."""
    actor = _actor(Role.NAVIGATOR)
    service = DemoSessionService(
        actor_repository=StaticActorRepository({Role.NAVIGATOR: actor}),
        secret=TEST_SESSION_SECRET,
        ttl_minutes=30,
        organization_id=actor.organization_id,
    )
    token = service.create_token(
        _authority_for_actor(actor), issued_at=1_000, expires_at=2_000
    )

    with pytest.raises(ValueError, match="Invalid or expired demo session"):
        service.verify_session(_resign_token(token, claim_update), now=1_000)


def test_invalid_token_http_refusal_suppresses_parser_context() -> None:
    authority = _authority(Role.NAVIGATOR)
    service = DemoSessionService(
        actor_repository=None,
        secret=TEST_SESSION_SECRET,
        ttl_minutes=30,
        organization_id=authority.actor.organization_id,
        demo_actors={
            Role.NAVIGATOR: DemoActorSelection(user_id=authority.actor.user_id)
        },
    )
    token = service.create_token(authority, issued_at=1_000, expires_at=2_000)
    invalid_token = _resign_token(token, {}, remove_claims=("ver",))
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/v1/navigator/queue",
            "raw_path": b"/v1/navigator/queue",
            "query_string": b"",
            "headers": [(b"cookie", f"ojcc_session={invalid_token}".encode())],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        current_actor(request, service, object())  # type: ignore[arg-type]

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid or expired demo session"
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


def test_demo_session_requires_an_environment_secret() -> None:
    """This fails if the API falls back to a production signing secret."""
    with pytest.raises(ValueError, match="DEMO_SESSION_SECRET"):
        DemoSessionService(
            actor_repository=StaticActorRepository({}),
            secret=None,
            ttl_minutes=30,
            organization_id=uuid4(),
        )


def test_demo_session_route_sets_a_local_http_only_cookie() -> None:
    """This fails if the session endpoint is unregistered or relaxes its cookie policy."""
    from app.api.demo_sessions import get_demo_session_service

    actor = _actor(Role.NAVIGATOR)
    session_service = DemoSessionService(
        actor_repository=StaticActorRepository({Role.NAVIGATOR: actor}),
        secret="test-only-signing-secret",
        ttl_minutes=30,
        organization_id=actor.organization_id,
        demo_actors={Role.NAVIGATOR: DemoActorSelection(user_id=actor.user_id)},
    )
    app.dependency_overrides[get_demo_session_service] = lambda: session_service

    async def create_session() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post("/v1/demo/session/navigator")

    try:
        response = asyncio.run(create_session())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 204
    cookie = response.headers["set-cookie"].lower()
    assert "ojcc_session=" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "path=/" in cookie
    assert "secure" not in cookie
    assert "max-age" not in cookie
    assert "expires" not in cookie
    stored_cookie = next(item for item in response.cookies.jar if item.name == "ojcc_session")
    assert stored_cookie.domain_specified is False
    assert stored_cookie.path == "/"
    assert stored_cookie.expires is None
    assert stored_cookie.secure is False
    assert stored_cookie.has_nonstandard_attr("HttpOnly")
    assert stored_cookie.get_nonstandard_attr("SameSite") == "lax"


def test_demo_session_route_rejects_missing_tenant_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This fails if missing tenant configuration reaches session issuance as a server error."""
    from app.api import demo_sessions

    monkeypatch.setattr(
        demo_sessions,
        "settings",
        Settings(demo_session_secret="test-only-signing-secret", demo_organization_id=None),
    )

    async def create_session() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post("/v1/demo/session/navigator")

    response = asyncio.run(create_session())

    assert response.status_code == 503
    assert response.json() == {"detail": "Demo sessions are not configured"}


def test_demo_session_configuration_refusal_suppresses_internal_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import demo_sessions

    monkeypatch.setattr(
        demo_sessions,
        "settings",
        Settings(
            demo_session_secret="test-only-signing-secret",
            demo_organization_id=uuid4(),
            demo_actors_json='{"navigator":',
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        demo_sessions.get_demo_session_service()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Demo sessions are not configured"
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


@pytest.mark.parametrize("ttl_minutes", [None, 0, 121])
def test_demo_session_route_rejects_invalid_ttl_configuration(
    monkeypatch: pytest.MonkeyPatch,
    ttl_minutes: int | None,
) -> None:
    """This fails if invalid TTL state reaches session-service construction."""
    from app.api import demo_sessions

    monkeypatch.setattr(
        demo_sessions,
        "settings",
        Settings(
            demo_session_secret="test-only-signing-secret",
            demo_session_ttl_minutes=ttl_minutes,
            demo_organization_id=uuid4(),
        ),
    )

    async def create_session() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post("/v1/demo/session/navigator")

    response = asyncio.run(create_session())

    assert response.status_code == 503
    assert response.json() == {"detail": "Demo sessions are not configured"}
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("configured_ttl", ["", "not-an-integer"])
def test_demo_session_route_sanitizes_malformed_ttl_environment(
    monkeypatch: pytest.MonkeyPatch,
    configured_ttl: str,
) -> None:
    from app.api import demo_sessions

    monkeypatch.setenv("DEMO_SESSION_TTL_MINUTES", configured_ttl)
    monkeypatch.setattr(
        demo_sessions,
        "settings",
        Settings(
            demo_session_secret=TEST_SESSION_SECRET,
            demo_organization_id=uuid4(),
        ),
    )

    async def create_session() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.post("/v1/demo/session/navigator")

    response = asyncio.run(create_session())

    assert response.status_code == 503
    assert response.json() == {"detail": "Demo sessions are not configured"}
    assert "set-cookie" not in response.headers


def test_demo_session_route_rejects_invalid_demo_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This fails if malformed tenant configuration is exposed during session issuance."""
    from app.api import demo_sessions

    monkeypatch.setenv("DEMO_ORGANIZATION_ID", "not-a-uuid")
    monkeypatch.setattr(
        demo_sessions,
        "settings",
        Settings(demo_session_secret="test-only-signing-secret"),
    )

    async def create_session() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post("/v1/demo/session/navigator")

    response = asyncio.run(create_session())

    assert response.status_code == 503
    assert response.json() == {"detail": "Demo sessions are not configured"}
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("ttl_minutes", [None, 0, 121])
def test_current_actor_factory_rejects_invalid_ttl_configuration(
    monkeypatch: pytest.MonkeyPatch,
    ttl_minutes: int | None,
) -> None:
    """This fails if current-session validation accepts malformed TTL configuration."""
    from app.auth import dependencies

    monkeypatch.setattr(
        dependencies,
        "settings",
        Settings(
            demo_session_secret="test-only-signing-secret",
            demo_session_ttl_minutes=ttl_minutes,
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        dependencies.get_current_demo_session_service()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Demo sessions are not configured"
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


def test_demo_session_route_is_registered_without_enabling_api_docs() -> None:
    """This fails if the router is not included in the application factory."""
    from app.api.demo_sessions import router

    assert "/v1/demo/session/{role}" in {
        getattr(route, "path", None) for route in router.routes
    }
    assert any(getattr(route, "original_router", None) is router for route in app.routes)
    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None


def test_demo_session_route_sets_secure_cookie_outside_local_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This fails if non-local sessions can be sent without the Secure flag."""
    from app.api import demo_sessions

    actor = _actor(Role.NAVIGATOR)
    session_service = DemoSessionService(
        actor_repository=StaticActorRepository({Role.NAVIGATOR: actor}),
        secret="test-only-signing-secret",
        ttl_minutes=30,
        organization_id=actor.organization_id,
        demo_actors={Role.NAVIGATOR: DemoActorSelection(user_id=actor.user_id)},
    )
    monkeypatch.setattr(demo_sessions, "settings", Settings(environment="staging"))
    app.dependency_overrides[demo_sessions.get_demo_session_service] = lambda: session_service

    async def create_session() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post("/v1/demo/session/navigator")

    try:
        response = asyncio.run(create_session())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 204
    assert "secure" in response.headers["set-cookie"].lower()
    stored_cookie = next(item for item in response.cookies.jar if item.name == "ojcc_session")
    assert stored_cookie.domain_specified is False
    assert stored_cookie.secure is True


def test_repeated_demo_session_issuance_replaces_the_host_only_cookie() -> None:
    from app.api import demo_sessions

    actor = _actor(Role.NAVIGATOR)
    session_service = DemoSessionService(
        actor_repository=StaticActorRepository({Role.NAVIGATOR: actor}),
        secret=TEST_SESSION_SECRET,
        ttl_minutes=30,
        organization_id=actor.organization_id,
        demo_actors={Role.NAVIGATOR: DemoActorSelection(user_id=actor.user_id)},
    )
    app.dependency_overrides[demo_sessions.get_demo_session_service] = (
        lambda: session_service
    )

    async def issue_twice() -> tuple[httpx.Response, httpx.Response, str, int]:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            first = await client.post("/v1/demo/session/navigator")
            old_cookie = client.cookies["ojcc_session"]
            second = await client.post("/v1/demo/session/navigator")
            current_cookie = client.cookies["ojcc_session"]
            cookie_count = len(
                [item for item in client.cookies.jar if item.name == "ojcc_session"]
            )
        assert current_cookie != old_cookie
        return first, second, current_cookie, cookie_count

    try:
        first, second, current_cookie, cookie_count = asyncio.run(issue_twice())
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == second.status_code == 204
    assert current_cookie
    assert cookie_count == 1
