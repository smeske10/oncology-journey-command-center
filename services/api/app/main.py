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
    if is_check_in_submission:
        validation_errors = error.errors()
        has_phi_error = any(
            str(item.get("ctx", {}).get("error", "")) == PUBLIC_DEMO_PHI_WARNING
            for item in validation_errors
        )
        has_only_answer_content_errors = bool(validation_errors) and all(
            len(location := item.get("loc", ())) > 1
            and location[0] == "body"
            and location[1] in {"answers", "free_text"}
            for item in validation_errors
        )
        if has_phi_error or has_only_answer_content_errors:
            code = "answers_invalid"
            message = (
                PUBLIC_DEMO_PHI_WARNING
                if has_phi_error
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
