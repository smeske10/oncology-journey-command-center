import { expect, test, vi } from "vitest";

import { bootstrapPatientCheckIn, submitCheckIn } from "../lib/api-client";

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
      Response.json(
        {
          detail: {
            code: "answers_invalid",
            message: "Answers must use known questionnaire link IDs",
          },
        },
        { status: 422 },
      ),
    ),
  );

  await expect(
    submitCheckIn("a6c304e8-8070-4a65-90cc-168a4fb6d998", {
      questionnaire_version: "breast-active-v1",
      answers: [{ link_id: "nausea_change", value: "worse" }],
    }),
  ).rejects.toMatchObject({ kind: "correction" });
});

test.each(["questionnaire_stale", "definition_inactive", "correction_stale"])(
  "treats %s as configuration recovery rather than an answer correction",
  async (code) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        Response.json(
          { detail: { code, message: "Reload the current check-in." } },
          { status: 422 },
        ),
      ),
    );

    await expect(
      submitCheckIn("a6c304e8-8070-4a65-90cc-168a4fb6d998", {
        questionnaire_version: "breast-active-v1",
        answers: [{ link_id: "nausea_change", value: "worse" }],
      }),
    ).rejects.toMatchObject({ kind: "configuration", code });
  },
);

test("does not classify an unknown 422 response as a correctable answer", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      Response.json({ detail: [{ loc: ["path", "definition_id"], msg: "invalid" }] }, { status: 422 }),
    ),
  );

  await expect(
    submitCheckIn("not-a-valid-id", {
      questionnaire_version: "breast-active-v1",
      answers: [{ link_id: "nausea_change", value: "worse" }],
    }),
  ).rejects.toMatchObject({ kind: "configuration" });
});
