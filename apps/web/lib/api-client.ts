import type { components, paths } from "./api-types";

type CheckInSubmissionInput = components["schemas"]["CheckInSubmissionCreate"];
export type CheckInSubmissionResponse = components["schemas"]["CheckInSubmissionResponse"];
export type CheckInDefinitionResponse = components["schemas"]["CheckInDefinitionResponse"];
export type NavigatorQueueResponse = paths["/v1/navigator/queue"]["get"]["responses"][200]["content"]["application/json"];
export type NavigatorPatientCaseResponse = paths["/v1/navigator/patients/{patient_id}/case"]["get"]["responses"][200]["content"]["application/json"];
export type NavigatorNeedWorkspaceResponse = components["schemas"]["NavigatorNeedWorkspaceRead"];
export type ApprovalDecisionInput = components["schemas"]["ApprovalDecisionCreate"];
export type TaskCommandResponse = components["schemas"]["TaskCommandRead"];
export type OutcomePreviewResponse = components["schemas"]["OutcomePreviewRead"];
export type OutcomeCommandInput = components["schemas"]["OutcomeCommandCreate"];
export type OutcomeCommandResponse = components["schemas"]["OutcomeCommandRead"];
export type PatientFollowUpListResponse = components["schemas"]["PatientFollowUpListRead"];
export type FollowUpResponseInput = components["schemas"]["FollowUpResponseCreate"];
export type FollowUpResponseResult = components["schemas"]["FollowUpResponseRead"];
export type PatientTimelineResponse = components["schemas"]["PatientTimelineRead"];

export type ApiErrorKind = "configuration" | "correction" | "persistence";
export type ApiErrorCode =
  | "answers_invalid"
  | "configuration_invalid"
  | "correction_stale"
  | "definition_inactive"
  | "questionnaire_stale";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly kind: ApiErrorKind = "persistence",
    public readonly code?: string,
    public readonly status?: number,
  ) {
    super(message);
  }
}

export async function bootstrapPatientCheckIn(): Promise<CheckInDefinitionResponse> {
  await request("/api/v1/demo/session/supporting_actor", { method: "POST" });
  return request<CheckInDefinitionResponse>("/api/v1/patient/check-ins/current");
}

export async function submitCheckIn(
  definitionId: string,
  payload: CheckInSubmissionInput,
): Promise<CheckInSubmissionResponse> {
  return request<CheckInSubmissionResponse>(
    `/api/v1/patient/check-ins/${encodeURIComponent(definitionId)}/submissions`,
    {
      body: JSON.stringify(payload),
      headers: { "content-type": "application/json" },
      method: "POST",
    },
  );
}

export async function bootstrapNavigatorQueue(): Promise<NavigatorQueueResponse> {
  await request("/api/v1/demo/session/navigator", { method: "POST" });
  return getNavigatorQueue();
}

export async function getNavigatorQueue(signal?: AbortSignal): Promise<NavigatorQueueResponse> {
  return request<NavigatorQueueResponse>("/api/v1/navigator/queue", { signal });
}

export async function getNavigatorPatientCase(
  patientId: string,
  signal?: AbortSignal,
): Promise<NavigatorPatientCaseResponse> {
  return request<NavigatorPatientCaseResponse>(
    `/api/v1/navigator/patients/${encodeURIComponent(patientId)}/case`,
    { signal },
  );
}

export async function getNavigatorNeedWorkspace(
  needId: string,
  signal?: AbortSignal,
): Promise<NavigatorNeedWorkspaceResponse> {
  return request<NavigatorNeedWorkspaceResponse>(
    `/api/v1/navigator/needs/${encodeURIComponent(needId)}/workspace`,
    { signal },
  );
}

export async function decideProposal(
  proposalId: string,
  payload: ApprovalDecisionInput,
): Promise<components["schemas"]["ApprovalDecisionRead"]> {
  return request(`/api/v1/navigator/proposed-changes/${encodeURIComponent(proposalId)}/decisions`, {
    body: JSON.stringify(payload),
    headers: { "content-type": "application/json" },
    method: "POST",
  });
}

export async function claimTask(
  taskId: string,
  proposedChangeId: string,
  dueAt: string,
): Promise<TaskCommandResponse> {
  return request(`/api/v1/navigator/tasks/${encodeURIComponent(taskId)}/claim`, {
    body: JSON.stringify({ due_at: dueAt, proposed_change_id: proposedChangeId }),
    headers: { "content-type": "application/json" },
    method: "POST",
  });
}

export async function startTask(taskId: string): Promise<TaskCommandResponse> {
  return taskTransition(taskId, "start");
}

export async function completeTask(taskId: string): Promise<TaskCommandResponse> {
  return taskTransition(taskId, "complete");
}

export async function getOutcomePreview(needId: string): Promise<OutcomePreviewResponse> {
  return request(`/api/v1/navigator/needs/${encodeURIComponent(needId)}/outcome-preview`);
}

export async function recordOutcome(
  needId: string,
  payload: OutcomeCommandInput,
  idempotencyKey: string,
): Promise<OutcomeCommandResponse> {
  return request(`/api/v1/navigator/needs/${encodeURIComponent(needId)}/outcomes`, {
    body: JSON.stringify(payload),
    headers: { "content-type": "application/json", "Idempotency-Key": idempotencyKey },
    method: "POST",
  });
}

export async function getPatientFollowUps(): Promise<PatientFollowUpListResponse> {
  return request("/api/v1/patient/follow-ups");
}

export async function respondToFollowUp(
  requestId: string,
  payload: FollowUpResponseInput,
): Promise<FollowUpResponseResult> {
  return request(`/api/v1/patient/follow-ups/${encodeURIComponent(requestId)}/responses`, {
    body: JSON.stringify(payload),
    headers: { "content-type": "application/json" },
    method: "POST",
  });
}

export async function getPatientTimeline(): Promise<PatientTimelineResponse> {
  return request("/api/v1/patient/journey-timeline");
}

async function taskTransition(
  taskId: string,
  transition: "start" | "complete",
): Promise<TaskCommandResponse> {
  return request(`/api/v1/navigator/tasks/${encodeURIComponent(taskId)}/${transition}`, {
    body: "{}",
    headers: { "content-type": "application/json" },
    method: "POST",
  });
}

async function request<T = undefined>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, credentials: "include" });
  if (response.ok) {
    return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
  }
  const detail = await errorDetail(response);
  if (response.status === 422) {
    const kind = detail.code === "answers_invalid" ? "correction" : "configuration";
    throw new ApiError(detail.message, kind, detail.code, response.status);
  }
  if (response.status === 401 || response.status === 403 || response.status === 503) {
    throw new ApiError(detail.message, "configuration", detail.code, response.status);
  }
  throw new ApiError(detail.message, "persistence", detail.code, response.status);
}

type ErrorDetail = { code?: string; message: string };

async function errorDetail(response: Response): Promise<ErrorDetail> {
  try {
    const body = (await response.json()) as {
      detail?: string | { code?: string; message?: string };
    };
    if (typeof body.detail === "string") return { message: body.detail };
    if (body.detail && typeof body.detail.message === "string") {
      return {
        code: typeof body.detail.code === "string" ? body.detail.code : undefined,
        message: body.detail.message,
      };
    }
  } catch {
    // The public error message remains safe if a proxy or server returns a non-JSON error.
  }
  return {
    message: "We could not save your check-in. Your review is still available; please try again.",
  };
}
