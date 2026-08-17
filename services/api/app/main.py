from fastapi import FastAPI

from app.config import settings

app = FastAPI(
    title=settings.api_title,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
