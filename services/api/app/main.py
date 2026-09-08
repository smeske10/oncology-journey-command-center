from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.demo_sessions import router as demo_sessions_router
from app.api.navigator_outcomes import router as navigator_outcomes_router
from app.api.navigator_queue import router as navigator_queue_router
from app.api.patient_check_ins import router as patient_check_ins_router
from app.api.proposed_changes import router as proposed_changes_router
from app.api.safety_signals import router as safety_signals_router
from app.config import settings
from app.domain.check_ins import PUBLIC_DEMO_PHI_WARNING

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
app.include_router(safety_signals_router)
app.include_router(proposed_changes_router)


@app.exception_handler(RequestValidationError)
async def stable_check_in_validation_error(
    request: Request, error: RequestValidationError
) -> JSONResponse:
    is_check_in_submission = (
        request.method == "POST"
        and request.url.path.startswith("/v1/patient/check-ins/")
        and request.url.path.endswith("/submissions")
    )
    has_body_error = any(item.get("loc", (None,))[0] == "body" for item in error.errors())
    if is_check_in_submission:
        rendered_errors = " ".join(str(item) for item in error.errors())
        if has_body_error:
            code = "answers_invalid"
            message = (
                PUBLIC_DEMO_PHI_WARNING
                if "must not receive real health information" in rendered_errors
                else "Please review the answers and try again."
            )
        else:
            code = "configuration_invalid"
            message = "This check-in request is invalid. Reload the current check-in."
        return JSONResponse(
            status_code=422,
            content={"detail": {"code": code, "message": message}},
        )
    return await request_validation_exception_handler(request, error)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
