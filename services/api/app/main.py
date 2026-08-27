from fastapi import FastAPI

from app.api.demo_sessions import router as demo_sessions_router
from app.api.navigator_outcomes import router as navigator_outcomes_router
from app.api.navigator_queue import router as navigator_queue_router
from app.api.patient_check_ins import router as patient_check_ins_router
from app.config import settings

app = FastAPI(
    title=settings.api_title,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.include_router(demo_sessions_router)
app.include_router(patient_check_ins_router)
app.include_router(navigator_queue_router)
app.include_router(navigator_outcomes_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
