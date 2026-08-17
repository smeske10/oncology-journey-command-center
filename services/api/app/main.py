from fastapi import FastAPI

from app.api.demo_sessions import router as demo_sessions_router
from app.config import settings

app = FastAPI(
    title=settings.api_title,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.include_router(demo_sessions_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
