from enum import Enum


class UserRole(str, Enum):
    ADMINISTRATOR = "administrator"
    NAVIGATOR = "navigator"
    SUPPORTING_ACTOR = "supporting_actor"


class CareEpisodeStatus(str, Enum):
    ACTIVE = "active"
    CLOSED = "closed"


class CheckInStatus(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    PROCESSED = "processed"


class NeedStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class SafetySignalStatus(str, Enum):
    ACTIVE = "active"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class SafetySeverity(str, Enum):
    ROUTINE = "routine"
    URGENT = "urgent"
    EMERGENT = "emergent"


class NavigationTaskStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ApprovalStatus(str, Enum):
    APPROVED = "approved"
    EDITED = "edited"
    DECLINED = "declined"


class KnowledgeDocumentStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    RETIRED = "retired"


class AgentRunStatus(str, Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    MANUAL_REVIEW = "manual_review"


class OutcomeStatus(str, Enum):
    RESOLVED = "resolved"
    CLOSED_UNRESOLVED = "closed_unresolved"
