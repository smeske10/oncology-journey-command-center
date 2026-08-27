import asyncio

import httpx

from app.main import app


def test_health_returns_ok() -> None:
    async def request_health() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get("/health")

    response = asyncio.run(request_health())

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_application_exposes_health_and_demo_session_routes() -> None:
    from app.api.demo_sessions import router

    assert {route.path for route in app.routes if hasattr(route, "path")} == {"/health"}
    assert "/v1/demo/session/{role}" in {route.path for route in router.routes}
    assert any(getattr(route, "original_router", None) is router for route in app.routes)
