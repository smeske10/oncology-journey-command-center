import type { components } from "./api-types";

type CheckInSubmissionInput = components["schemas"]["CheckInSubmissionCreate"];
type CheckInSubmissionResponse = components["schemas"]["CheckInSubmissionResponse"];
export type CheckInDefinitionResponse = components["schemas"]["CheckInDefinitionResponse"];

export type ApiErrorKind = "configuration" | "correction" | "persistence";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly kind: ApiErrorKind = "persistence",
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

async function request<T = undefined>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, credentials: "include" });
  if (response.ok) {
    return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
  }
  const detail = await errorDetail(response);
  if (response.status === 422) throw new ApiError(detail, "correction");
  if (response.status === 401 || response.status === 403 || response.status === 503) {
    throw new ApiError(detail, "configuration");
  }
  throw new ApiError(detail, "persistence");
}

async function errorDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string };
    if (body.detail) return body.detail;
  } catch {
    // The public error message remains safe if a proxy or server returns a non-JSON error.
  }
  return "We could not save your check-in. Your review is still available; please try again.";
}
