import asyncio
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.auth.dependencies import require_role
from app.auth.models import CurrentActor, Role
from app.auth.service import ActorRepository, DemoSessionService
from app.config import Settings
from app.main import app


def _actor(role: Role) -> CurrentActor:
    return CurrentActor(user_id=uuid4(), organization_id=uuid4(), role=role)


class StaticActorRepository(ActorRepository):
    def __init__(self, actors: dict[Role, CurrentActor]) -> None:
        self._actors = actors

    def find_active_actor(
        self, *, organization_id: UUID, role: Role
    ) -> CurrentActor | None:
        actor = self._actors.get(role)
        if actor is None or actor.organization_id != organization_id:
            return None
        return actor


def test_patient_facing_supporting_actor_cannot_use_navigator_permission() -> None:
    """This fails if require_role accidentally permits every authenticated actor."""
    dependency = require_role(Role.NAVIGATOR)

    with pytest.raises(HTTPException) as exc_info:
        dependency(_actor(Role.SUPPORTING_ACTOR))

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Role not permitted"


def test_demo_sessions_are_signed_and_tamper_evident() -> None:
    """This fails if the token signature is omitted or its comparison is not enforced."""
    actor = _actor(Role.NAVIGATOR)
    service = DemoSessionService(
        actor_repository=StaticActorRepository({Role.NAVIGATOR: actor}),
        secret="test-only-signing-secret",
        ttl_minutes=30,
        organization_id=actor.organization_id,
    )

    token = service.create_token(actor)
    tampered_token = f"{token[:-1]}{'A' if token[-1] != 'A' else 'B'}"

    assert service.current_actor(token) == actor
    with pytest.raises(ValueError, match="Invalid or expired demo session"):
        service.current_actor(tampered_token)


def test_demo_sessions_reject_expired_or_overlong_tokens() -> None:
    """This fails if token expiry or the two-hour maximum lifetime is not checked."""
    actor = _actor(Role.NAVIGATOR)
    service = DemoSessionService(
        actor_repository=StaticActorRepository({Role.NAVIGATOR: actor}),
        secret="test-only-signing-secret",
        ttl_minutes=120,
        organization_id=actor.organization_id,
    )

    expired_token = service.create_token(actor, issued_at=1_000, expires_at=1_001)
    overlong_token = service.create_token(actor, issued_at=1_000, expires_at=8_201)

    with pytest.raises(ValueError, match="Invalid or expired demo session"):
        service.current_actor(expired_token, now=1_002)
    with pytest.raises(ValueError, match="Invalid or expired demo session"):
        service.current_actor(overlong_token, now=1_001)


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


def test_demo_session_route_is_registered_without_enabling_api_docs() -> None:
    """This fails if the router is not included in the application factory."""
    from app.api.demo_sessions import router

    assert "/v1/demo/session/{role}" in {route.path for route in router.routes}
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
