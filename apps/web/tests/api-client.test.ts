import { expect, test, vi } from "vitest";

import { ApiError, bootstrapPatientCheckIn, submitCheckIn } from "../lib/api-client";

test("bootstraps a synthetic session and loads the generated current-check-in contract", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(null, { status: 204 }))
    .mockResolvedValueOnce(
      Response.json({
        id: "a6c304e8-8070-4a65-90cc-168a4fb6d998",
        title: "Today’s check-in",
        questionnaire_version: "breast-active-v1",
        questions: [],
      }),
    );
  vi.stubGlobal("fetch", fetchMock);

  const definition = await bootstrapPatientCheckIn();

  expect(definition.questionnaire_version).toBe("breast-active-v1");
  expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/v1/demo/session/supporting_actor", {
    credentials: "include",
    method: "POST",
  });
  expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/v1/patient/check-ins/current", {
    credentials: "include",
  });
});

test("reports a correction error separately from a persistence failure", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      Response.json({ detail: "Answers must use known questionnaire link IDs" }, { status: 422 }),
    ),
  );

  await expect(
    submitCheckIn("a6c304e8-8070-4a65-90cc-168a4fb6d998", {
      questionnaire_version: "breast-active-v1",
      answers: [{ link_id: "nausea_change", value: "worse" }],
    }),
  ).rejects.toMatchObject<ApiError>({ kind: "correction" });
});
