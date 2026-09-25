"""Navigator commands; all evidence and creation authority are derived by PostgreSQL."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.auth.dependencies import require_role
from app.auth.models import CurrentActor, Role
from app.db.models import CheckInSubmission
from app.db.session import get_session
from app.domain.need_creation import (
    NeedCreationDecisionCreate,
    NeedCreationDecisionRead,
    NeedCreationHistoryRead,
    NeedCreationProposalCreate,
    NeedCreationProposalRead,
)

router = APIRouter(prefix="/v1/navigator/need-creation-proposals", tags=["navigator"])


def _database_error(session: Session, error: DBAPIError) -> None:
    session.rollback()
    sqlstate = getattr(error.orig, "sqlstate", None)
    message = getattr(getattr(error.orig, "diag", None), "message_primary", "")
    if sqlstate == "42501":
        raise HTTPException(
            403,
            detail={"code": "authority_invalid", "message": "Navigator authority is unavailable."},
        ) from None
    if sqlstate in {"23514", "23505", "23503"}:
        code = (
            message
            if message in {"stale_evidence", "already_created", "request_conflict"}
            else "creation_conflict"
        )
        raise HTTPException(
            409,
            detail={
                "code": code,
                "message": "The report changed or cannot be created. Refresh and review it again.",
            },
        ) from None
    raise HTTPException(
        503,
        detail={
            "code": "creation_unavailable",
            "message": "The decision could not be confirmed. Retry the same request.",
        },
    ) from None


def _proposals(session: Session, organization_id: UUID, proposal_id: UUID | None = None):
    submissions = session.scalars(
        select(CheckInSubmission).where(CheckInSubmission.organization_id == organization_id)
    ).all()
    by_id = {s.id: s for s in submissions}
    children: dict[UUID, list[CheckInSubmission]] = {}
    for submission in submissions:
        if submission.supersedes_submission_id:
            children.setdefault(submission.supersedes_submission_id, []).append(submission)
    rows = (
        session.execute(
            text("""
        SELECT p.*, patient.display_name AS patient_display_name,
            EXISTS (SELECT 1 FROM need_creation_decision d
                WHERE d.organization_id=p.organization_id AND d.chain_root_id=p.chain_root_id
                AND d.outcome='created') AS chain_created
        FROM need_creation_proposal p
        JOIN synthetic_patient patient ON patient.organization_id=p.organization_id
            AND patient.id=p.patient_id
        WHERE p.organization_id=:org AND (CAST(:id AS uuid) IS NULL OR p.id=:id)
        ORDER BY p.proposed_at DESC, p.id
    """),
            dict(org=organization_id, id=proposal_id),
        )
        .mappings()
        .all()
    )
    decisions = (
        session.execute(
            text("""
        SELECT * FROM need_creation_decision WHERE organization_id=:org
            AND (CAST(:id AS uuid) IS NULL OR proposal_id=:id)
        ORDER BY authorized_at, id
    """),
            dict(org=organization_id, id=proposal_id),
        )
        .mappings()
        .all()
    )
    by_proposal: dict[UUID, list[NeedCreationDecisionRead]] = {}
    for decision in decisions:
        by_proposal.setdefault(decision["proposal_id"], []).append(
            NeedCreationDecisionRead.model_validate(dict(decision))
        )
    result = []
    for row in rows:
        leaf = by_id.get(row["chain_root_id"])
        visited: set[UUID] = set()
        while leaf is not None:
            if (
                leaf.id in visited
                or leaf.patient_id != row["patient_id"]
                or leaf.care_episode_id != row["care_episode_id"]
            ):
                leaf = None
                break
            visited.add(leaf.id)
            successors = children.get(leaf.id, [])
            if not successors:
                break
            leaf = successors[0] if len(successors) == 1 else None
        answer_text = "Current answer unavailable; review the check-in history."
        source_changed = leaf is None or leaf.id != row["source_submission_id"]
        if leaf is not None and isinstance(leaf.answers, dict):
            items = leaf.answers.get("items")
            if isinstance(items, list):
                answers = [
                    a for a in items if isinstance(a, dict) and a.get("link_id") == "transportation"
                ]
                if len(answers) == 1 and answers[0].get("value") in ("yes", "no"):
                    answer_text = answers[0]["value"]
        history = by_proposal.get(row["id"], [])
        terminal = {d.outcome for d in history} & {"created", "declined"}
        if len(terminal) > 1:
            raise HTTPException(503, "Proposal history is inconsistent")
        state = (
            next(iter(terminal))
            if terminal
            else (
                "stale"
                if source_changed
                else "already_created"
                if row["chain_created"]
                else "pending"
            )
        )
        result.append(
            NeedCreationProposalRead.model_validate(
                dict(row)
                | {
                    "state": state,
                    "source_changed": source_changed,
                    "decisions": history,
                    "current_source_submission_id": leaf.id if leaf else None,
                    "current_answer_text": answer_text,
                }
            )
        )
    return result


@router.get("", response_model=NeedCreationHistoryRead)
def get_need_creation_history(
    response: Response,
    actor: CurrentActor = Depends(require_role(Role.NAVIGATOR)),
    session: Session = Depends(get_session),
) -> NeedCreationHistoryRead:
    response.headers["Cache-Control"] = "no-store"
    return NeedCreationHistoryRead(proposals=_proposals(session, actor.organization_id))


@router.post("", response_model=NeedCreationProposalRead)
def create_need_proposal(
    payload: NeedCreationProposalCreate,
    response: Response,
    actor: CurrentActor = Depends(require_role(Role.NAVIGATOR)),
    session: Session = Depends(get_session),
) -> NeedCreationProposalRead:
    response.headers["Cache-Control"] = "no-store"
    try:
        session.execute(
            text("""
            INSERT INTO need_creation_proposal
                (id,organization_id,chain_root_id,source_submission_id,proposed_by_user_id,rationale)
            VALUES (:id,:org,:root,:leaf,:actor,:rationale)
        """),
            dict(
                id=payload.request_id,
                org=actor.organization_id,
                root=payload.chain_root_id,
                leaf=payload.source_submission_id,
                actor=actor.user_id,
                rationale=payload.rationale,
            ),
        )
        session.commit()
    except DBAPIError as error:
        _database_error(session, error)
    rows = _proposals(session, actor.organization_id, payload.request_id)
    if len(rows) != 1:
        raise HTTPException(503, "Proposal could not be confirmed")
    return rows[0]


@router.post("/{proposal_id}/decisions", response_model=NeedCreationDecisionRead)
def create_need_decision(
    proposal_id: UUID,
    payload: NeedCreationDecisionCreate,
    response: Response,
    actor: CurrentActor = Depends(require_role(Role.NAVIGATOR)),
    session: Session = Depends(get_session),
) -> NeedCreationDecisionRead:
    response.headers["Cache-Control"] = "no-store"
    if (
        session.scalar(
            text("""SELECT id FROM need_creation_proposal
        WHERE organization_id=:org AND id=:id"""),
            dict(org=actor.organization_id, id=proposal_id),
        )
        is None
    ):
        raise HTTPException(404, "Proposal not found")
    try:
        session.execute(
            text("""
            INSERT INTO need_creation_decision
                (id,organization_id,proposal_id,authorized_by_user_id,decision,reason)
            VALUES (:id,:org,:proposal,:actor,:decision,:reason)
        """),
            dict(
                id=payload.request_id,
                org=actor.organization_id,
                proposal=proposal_id,
                actor=actor.user_id,
                decision=payload.decision,
                reason=payload.reason,
            ),
        )
        row = (
            session.execute(
                text("""SELECT * FROM need_creation_decision
            WHERE organization_id=:org AND id=:id"""),
                dict(org=actor.organization_id, id=payload.request_id),
            )
            .mappings()
            .one()
        )
        result = NeedCreationDecisionRead.model_validate(dict(row))
        # Refused attempts and their audit events must survive the HTTP conflict.
        session.commit()
    except DBAPIError as error:
        _database_error(session, error)
        raise AssertionError("unreachable") from error
    if result.outcome not in {"created", "declined"}:
        raise HTTPException(
            409,
            detail={
                "code": result.outcome,
                "message": "The decision was recorded but no need was created. Refresh to review.",
            },
        )
    return result
