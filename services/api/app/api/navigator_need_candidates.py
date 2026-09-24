"""Navigator-only, organization-scoped candidate evidence preview."""

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.auth.dependencies import require_role
from app.auth.models import CurrentActor, Role
from app.db.repositories import SqlAlchemyNavigatorRepository
from app.db.session import get_session
from app.domain.need_candidates import NavigatorNeedCandidatesRead, build_need_candidates

router = APIRouter(prefix="/v1/navigator", tags=["navigator"])


@router.get("/need-candidates", response_model=NavigatorNeedCandidatesRead)
def get_navigator_need_candidates(
    response: Response,
    actor: CurrentActor = Depends(require_role(Role.NAVIGATOR)),
    session: Session = Depends(get_session),
) -> NavigatorNeedCandidatesRead:
    response.headers["Cache-Control"] = "no-store"
    repository = SqlAlchemyNavigatorRepository(session)
    submissions = repository.list_candidate_submissions(organization_id=actor.organization_id)
    return build_need_candidates(
        organization_id=actor.organization_id,
        submissions=submissions,
        definitions=repository.list_check_in_definitions(
            definition_ids={row.check_in_definition_id for row in submissions},
            organization_id=actor.organization_id,
        ),
        patients=repository.list_candidate_patients(organization_id=actor.organization_id),
        needs=repository.list_candidate_related_needs(organization_id=actor.organization_id),
    )
