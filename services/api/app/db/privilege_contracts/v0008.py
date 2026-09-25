"""Revision 0008 extends the frozen 0007 contract with guarded creation commands."""

from . import v0007

REVISION = "0008_need_creation_approval"
APPLICATION_GROUP = v0007.APPLICATION_GROUP
APPROVED_PUBLIC_EXECUTE_EXTENSIONS = v0007.APPROVED_PUBLIC_EXECUTE_EXTENSIONS
APPLICATION_RELKINDS = v0007.APPLICATION_RELKINDS
TABLE_PRIVILEGES = v0007.TABLE_PRIVILEGES
COLUMN_PRIVILEGES = v0007.COLUMN_PRIVILEGES
INSERT_RELATIONS = (*v0007.INSERT_RELATIONS, "need_creation_proposal", "need_creation_decision")
UPDATE_RELATIONS = v0007.UPDATE_RELATIONS
CATALOG_RELATIONS = (
    *v0007.CATALOG_RELATIONS,
    "need_creation_policy",
    "need_creation_proposal",
    "need_creation_decision",
)
RUNTIME_SELECT_RELATIONS = CATALOG_RELATIONS
EXPECTED_RELATION_KINDS = (
    *v0007.EXPECTED_RELATION_KINDS,
    ("need_creation_policy", "r"),
    ("need_creation_proposal", "r"),
    ("need_creation_decision", "r"),
)
FUNCTION_IDENTITIES = (
    *v0007.FUNCTION_IDENTITIES,
    ("need_creation_chain", "p_org uuid, p_leaf uuid"),
    ("need_creation_evidence", "p_org uuid, p_root uuid, p_leaf uuid"),
    ("need_creation_authority", "p_org uuid, p_user uuid"),
    ("guard_need_creation_proposal", ""),
    ("guard_need_creation_decision", ""),
    ("guard_need_creation_correction", ""),
    ("guard_need_creation_authority", ""),
    ("lock_need_creation_authority", ""),
)
