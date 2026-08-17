"use client";

import { CheckInFlow, type PatientCheckInDefinition } from "../../../components/patient/check-in-flow";
import { submitCheckIn } from "../../../lib/api-client";

const definition: PatientCheckInDefinition = {
  id: "00000000-0000-7000-8000-000000000001",
  title: "Today’s check-in",
  questionnaireVersion: "breast-active-v1",
  questions: [
    {
      linkId: "nausea_change",
      label: "Since your last check-in, is your nausea better, the same, or worse?",
      options: [
        { value: "better", label: "It is better" },
        { value: "same", label: "About the same" },
        { value: "worse", label: "It is worse" },
      ],
    },
  ],
};

export default function PatientDemoPage() {
  return <CheckInFlow definition={definition} onSubmit={(payload) => submitCheckIn(definition.id, payload)} />;
}
