from enum import Enum


class UserRole(str, Enum):
    ADMINISTRATOR = "administrator"
    NAVIGATOR = "navigator"
    SUPPORTING_ACTOR = "supporting_actor"


class CareEpisodeStatus(str, Enum):
    ACTIVE = "active"
    CLOSED = "closed"


class CheckInStatus(str, Enum):
    SUBMITTED = "submitted"
    PROCESSED = "processed"


class SubmissionSource(str, Enum):
    PATIENT = "patient"
    AUTHORIZED_PROXY = "authorized_proxy"
    CLINICIAN = "clinician"
    IMPORT = "import"


class NeedStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"


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
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TaskCancellationReason(str, Enum):
    NEED_CLOSED = "need_closed"


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


class OutcomeDisposition(str, Enum):
    RESOLVED = "resolved"
    CLOSED_UNRESOLVED = "closed_unresolved"
