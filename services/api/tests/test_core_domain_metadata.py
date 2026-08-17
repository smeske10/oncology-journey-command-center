from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.db.base import Base
from app.db.models import ReportedNeed


def test_core_domain_metadata_uses_postgresql_jsonb_and_tenant_first_indexes() -> None:
    expected_tables = {
        "organization",
        "user_account",
        "role_assignment",
        "synthetic_patient",
        "care_episode",
        "pathway_definition",
        "check_in_definition",
        "check_in_submission",
        "reported_need",
        "safety_signal",
        "navigation_task",
        "approval_decision",
        "resource",
        "knowledge_document",
        "agent_run",
        "outcome",
        "audit_event",
    }

    assert expected_tables <= set(Base.metadata.tables)
    assert "JSONB" in str(CreateTable(ReportedNeed.__table__).compile(dialect=postgresql.dialect()))

    tenant_tables = [
        table for table in Base.metadata.tables.values() if "organization_id" in table.c
    ]
    for table in tenant_tables:
        for index in table.indexes:
            assert index.expressions[0].name == "organization_id"
