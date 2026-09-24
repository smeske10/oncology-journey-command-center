"""Read-only transportation evidence preview; never a durable review decision."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from typing import Literal, TypedDict
from uuid import UUID

from pydantic import BaseModel

from app.db.models import CheckInDefinition, CheckInSubmission, ReportedNeed, SyntheticPatient
from app.domain.needs import NeedFactory


class CandidateEvidenceRead(BaseModel):
    field: Literal["transportation"] = "transportation"
    question: str
    value: Literal["yes"] = "yes"
    text: str


class CandidateRelatedNeedRead(BaseModel):
    id: UUID
    kind: str
    effective_state: str
    source_submission_id: UUID | None
    reopened_from_need_id: UUID | None


class NeedCandidateRead(BaseModel):
    patient_id: UUID
    patient_display_name: str
    care_episode_id: UUID
    chain_root_id: UUID
    source_submission_id: UUID
    check_in_at: datetime
    source_submitted_at: datetime
    is_correction: bool
    evidence: CandidateEvidenceRead
    linked_needs: list[CandidateRelatedNeedRead]
    other_transportation_needs: list[CandidateRelatedNeedRead]


class CandidateUnavailableRead(BaseModel):
    patient_id: UUID
    patient_display_name: str
    care_episode_id: UUID
    chain_root_id: UUID | None = None
    reason: Literal["ambiguous_lineage", "unsupported_questionnaire", "ambiguous_answer"]


class NavigatorNeedCandidatesRead(BaseModel):
    candidates: list[NeedCandidateRead]
    unavailable: list[CandidateUnavailableRead]


class _CandidateContext(TypedDict):
    patient_id: UUID
    patient_display_name: str
    care_episode_id: UUID
    chain_root_id: UUID


def _supported_question(definition: CheckInDefinition | None, answers: dict) -> str | None:
    if definition is None or definition.slug != "weekly-synthetic-check-in":
        return None
    questionnaire = definition.questionnaire
    if (
        definition.version != 2
        or not isinstance(questionnaire, dict)
        or questionnaire.get("version") != "weekly-synthetic-check-in-v2"
        or answers.get("questionnaire_version") != "weekly-synthetic-check-in-v2"
    ):
        return None
    questions = questionnaire.get("questions")
    if not isinstance(questions, list):
        return None
    matching = [
        q for q in questions if isinstance(q, dict) and q.get("link_id") == "transportation"
    ]
    if len(matching) != 1:
        return None
    (question,) = matching
    if question.get("label") != "Need synthetic transportation support?" or question.get(
        "options"
    ) != [
        {"value": "yes", "label": "Yes"},
        {"value": "no", "label": "No"},
    ]:
        return None
    return question["label"]


def build_need_candidates(
    *,
    organization_id: UUID,
    submissions: Sequence[CheckInSubmission],
    definitions: Sequence[CheckInDefinition],
    patients: Sequence[SyntheticPatient],
    needs: Sequence[tuple[ReportedNeed, str]],
) -> NavigatorNeedCandidatesRead:
    """Validate whole chains before projecting their leaves; scope every supplied record."""
    result = NavigatorNeedCandidatesRead(candidates=[], unavailable=[])
    by_id: dict[UUID, list[CheckInSubmission]] = defaultdict(list)
    neighbors: dict[UUID, set[UUID]] = defaultdict(set)
    definitions_by_id: dict[UUID, list[CheckInDefinition]] = defaultdict(list)
    patients_by_id: dict[UUID, list[SyntheticPatient]] = defaultdict(list)
    for row in submissions:
        if row.organization_id == organization_id:
            by_id[row.id].append(row)
            if row.supersedes_submission_id is not None:
                neighbors[row.id].add(row.supersedes_submission_id)
                neighbors[row.supersedes_submission_id].add(row.id)
    for definition in definitions:
        if definition.organization_id == organization_id:
            definitions_by_id[definition.id].append(definition)
    for patient in patients:
        if patient.organization_id == organization_id:
            patients_by_id[patient.id].append(patient)

    visited: set[UUID] = set()
    for submission_id in by_id:
        if submission_id in visited:
            continue
        component: set[UUID] = set()
        pending = [submission_id]
        while pending:
            current = pending.pop()
            if current in component:
                continue
            component.add(current)
            pending.extend(neighbors[current] - component)
        visited.update(component)
        rows = [row for key in component for row in by_id.get(key, [])]
        scopes = {(row.patient_id, row.care_episode_id) for row in rows}
        roots = [row for row in rows if row.supersedes_submission_id is None]
        successors: dict[UUID, list[CheckInSubmission]] = defaultdict(list)
        for row in rows:
            if row.supersedes_submission_id is not None:
                successors[row.supersedes_submission_id].append(row)
        valid = (
            all(len(by_id.get(key, [])) == 1 for key in component)
            and len(scopes) == 1
            and len(roots) == 1
            and all(len(values) == 1 for values in successors.values())
        )
        if not valid:
            for patient_id, episode_id in sorted(scopes, key=str):
                matches = patients_by_id[patient_id]
                name = matches[0].display_name if len(matches) == 1 else "Patient unavailable"
                result.unavailable.append(
                    CandidateUnavailableRead(
                        patient_id=patient_id,
                        patient_display_name=name,
                        care_episode_id=episode_id,
                        reason="ambiguous_lineage",
                    )
                )
            continue
        (root,) = roots
        chain = [root]
        while chain[-1].id in successors:
            (successor,) = successors[chain[-1].id]
            chain.append(successor)
        leaf = chain[-1]
        patient_matches = patients_by_id[leaf.patient_id]
        context = _CandidateContext(
            patient_id=leaf.patient_id,
            patient_display_name=(
                patient_matches[0].display_name
                if len(patient_matches) == 1
                else "Patient unavailable"
            ),
            care_episode_id=leaf.care_episode_id,
            chain_root_id=root.id,
        )
        if len(patient_matches) != 1:
            result.unavailable.append(
                CandidateUnavailableRead(**context, reason="ambiguous_lineage")
            )
            continue
        definition_matches = definitions_by_id[leaf.check_in_definition_id]
        definition = definition_matches[0] if len(definition_matches) == 1 else None
        answers = leaf.answers if isinstance(leaf.answers, dict) else {}
        question = _supported_question(definition, answers)
        if question is None:
            result.unavailable.append(
                CandidateUnavailableRead(
                    **context,
                    reason="unsupported_questionnaire",
                )
            )
            continue
        items = answers.get("items", [])
        matching = (
            [
                item
                for item in items
                if isinstance(item, dict) and item.get("link_id") == "transportation"
            ]
            if isinstance(items, list)
            else []
        )
        if len(matching) > 1 or not isinstance(items, list):
            result.unavailable.append(
                CandidateUnavailableRead(**context, reason="ambiguous_answer")
            )
            continue
        if not matching or matching[0].get("value") != "yes":
            continue
        # Invoke the factory only with the validated field; other answers are outside this preview.
        evidence_submission = CheckInSubmission(id=leaf.id, answers={"items": matching})
        (extracted,) = NeedFactory.from_submission(evidence_submission)
        (evidence,) = extracted.evidence
        linked, other = [], []
        for need, state in needs:
            if (
                need.organization_id != organization_id
                or need.patient_id != leaf.patient_id
                or need.care_episode_id != leaf.care_episode_id
            ):
                continue
            target = linked if need.source_submission_id in component else other
            if target is other and need.kind != "transportation":
                continue
            target.append(
                CandidateRelatedNeedRead(
                    id=need.id,
                    kind=need.kind,
                    effective_state=state,
                    source_submission_id=need.source_submission_id,
                    reopened_from_need_id=need.reopened_from_need_id,
                )
            )
        result.candidates.append(
            NeedCandidateRead(
                **context,
                source_submission_id=leaf.id,
                check_in_at=root.submitted_at,
                source_submitted_at=leaf.submitted_at,
                is_correction=leaf.id != root.id,
                evidence=CandidateEvidenceRead(question=question, text=evidence.text),
                linked_needs=sorted(linked, key=lambda need: str(need.id)),
                other_transportation_needs=sorted(other, key=lambda need: str(need.id)),
            )
        )
    result.candidates.sort(key=lambda c: (c.check_in_at, str(c.chain_root_id)), reverse=True)
    result.unavailable.sort(key=lambda u: (str(u.patient_id), str(u.chain_root_id), u.reason))
    return result
