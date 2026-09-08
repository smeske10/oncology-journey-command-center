import type { components, paths } from "./api-types";

type CheckInSubmissionInput = components["schemas"]["CheckInSubmissionCreate"];
export type CheckInSubmissionResponse = components["schemas"]["CheckInSubmissionResponse"];
export type CheckInDefinitionResponse = components["schemas"]["CheckInDefinitionResponse"];
export type NavigatorQueueResponse = paths["/v1/navigator/queue"]["get"]["responses"][200]["content"]["application/json"];
export type NavigatorPatientCaseResponse = paths["/v1/navigator/patients/{patient_id}/case"]["get"]["responses"][200]["content"]["application/json"];

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
    public readonly code?: ApiErrorCode,
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
  return request<NavigatorQueueResponse>("/api/v1/navigator/queue");
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

async function request<T = undefined>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, credentials: "include" });
  if (response.ok) {
    return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
  }
  const detail = await errorDetail(response);
  if (response.status === 422) {
    const kind = detail.code === "answers_invalid" ? "correction" : "configuration";
    throw new ApiError(detail.message, kind, detail.code);
  }
  if (response.status === 401 || response.status === 403 || response.status === 503) {
    throw new ApiError(detail.message, "configuration", detail.code);
  }
  throw new ApiError(detail.message, "persistence", detail.code);
}

type ErrorDetail = { code?: ApiErrorCode; message: string };

async function errorDetail(response: Response): Promise<ErrorDetail> {
  try {
    const body = (await response.json()) as {
      detail?: string | { code?: string; message?: string };
    };
    if (typeof body.detail === "string") return { message: body.detail };
    if (body.detail && typeof body.detail.message === "string") {
      return {
        code: isApiErrorCode(body.detail.code) ? body.detail.code : undefined,
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

function isApiErrorCode(value: unknown): value is ApiErrorCode {
  return [
    "answers_invalid",
    "configuration_invalid",
    "correction_stale",
    "definition_inactive",
    "questionnaire_stale",
  ].includes(String(value));
}
