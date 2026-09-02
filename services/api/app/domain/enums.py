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
    OPEN = "open"
    ACTIVE = "open"
    ACKNOWLEDGED = "acknowledged"


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


class ApprovalDecisionValue(str, Enum):
    APPROVED = "approved"
    DECLINED = "declined"


ApprovalStatus = ApprovalDecisionValue


class SignalRuleKind(str, Enum):
    DETERMINISTIC = "deterministic"
    HUMAN_ESCALATION = "human_escalation"


class ApprovalChangeType(str, Enum):
    DISMISS_SIGNAL = "dismiss_signal"
    OVERRIDE_SIGNAL_SEVERITY = "override_signal_severity"
    AUTHORIZE_NAVIGATION_TASK = "authorize_navigation_task"
    AUTHORIZE_PATIENT_MESSAGE = "authorize_patient_message"


class EffectiveProposalState(str, Enum):
    SUPERSEDED = "superseded"
    DECLINED = "declined"
    APPROVED = "approved"
    PENDING = "pending"


class EffectiveSafetySignalState(str, Enum):
    DISMISSED = "dismissed"
    RESOLVED = "resolved"
    ACKNOWLEDGED = "acknowledged"
    OPEN = "open"


class AgentRunStatus(str, Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    MANUAL_REVIEW = "manual_review"


class ManualReviewTaskState(str, Enum):
    OPEN = "open"
    ASSIGNED = "assigned"
    RESOLVED = "resolved"


class AuditActorType(str, Enum):
    USER = "user"
    AGENT = "agent"
    POLICY = "policy"
    SYSTEM = "system"


class OutcomeDisposition(str, Enum):
    RESOLVED = "resolved"
    CLOSED_UNRESOLVED = "closed_unresolved"
