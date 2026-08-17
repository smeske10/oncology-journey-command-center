import type { components } from "./api-types";

type CheckInSubmissionInput = components["schemas"]["CheckInSubmissionCreate"];
type CheckInSubmissionResponse = components["schemas"]["CheckInSubmissionResponse"];

export class ApiError extends Error {}

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export async function submitCheckIn(
  definitionId: string,
  payload: CheckInSubmissionInput,
): Promise<CheckInSubmissionResponse> {
  const response = await fetch(
    `${apiBaseUrl}/v1/patient/check-ins/${encodeURIComponent(definitionId)}/submissions`,
    {
      body: JSON.stringify(payload),
      credentials: "include",
      headers: { "content-type": "application/json" },
      method: "POST",
    },
  );
  if (!response.ok) {
    throw new ApiError("Check-in submission was not saved");
  }
  return response.json();
}
