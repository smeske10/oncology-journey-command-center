"use client";

import { useCallback, useEffect, useState } from "react";
import type { CSSProperties } from "react";

import { PatientCase } from "../../../components/navigator/patient-case";
import { WorkQueue, type NavigatorQueueItem } from "../../../components/navigator/work-queue";
import {
  ApiError,
  bootstrapNavigatorQueue,
  getNavigatorPatientCase,
  type NavigatorPatientCaseResponse,
} from "../../../lib/api-client";

export default function NavigatorDemoPage() {
  const [items, setItems] = useState<NavigatorQueueItem[]>([]);
  const [queueError, setQueueError] = useState("");
  const [loadingQueue, setLoadingQueue] = useState(true);
  const [selected, setSelected] = useState<NavigatorQueueItem>();
  const [caseData, setCaseData] = useState<NavigatorPatientCaseResponse>();
  const [caseError, setCaseError] = useState("");
  const [loadingCase, setLoadingCase] = useState(false);
  const selectedNeedId = selected?.need_id;
  const selectedPatientId = selected?.patient_id;

  const selectQueueItem = useCallback((item: NavigatorQueueItem) => {
    setSelected(item);
    setCaseData(undefined);
    setCaseError("");
    setLoadingCase(true);
  }, []);

  useEffect(() => {
    void bootstrapNavigatorQueue()
      .then((response) => {
        const queueItems = response.items;
        setItems(queueItems);
        if (queueItems[0]) selectQueueItem(queueItems[0]);
      })
      .catch((error: unknown) => setQueueError(readError(error, "The navigator queue could not be loaded.")))
      .finally(() => setLoadingQueue(false));
  }, [selectQueueItem]);

  useEffect(() => {
    if (!selectedNeedId || !selectedPatientId) return;
    const controller = new AbortController();
    void getNavigatorPatientCase(selectedPatientId, controller.signal)
      .then((response) => {
        if (!controller.signal.aborted) setCaseData(response);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setCaseError(readError(error, "The patient case could not be loaded."));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingCase(false);
      });
    return () => controller.abort();
  }, [selectedNeedId, selectedPatientId]);

  return (
    <main style={mainStyle}>
      <header>
        <p style={eyebrow}>Synthetic demo · navigator workspace</p>
        <h1>Navigator command center</h1>
        <p>Review exact patient-reported evidence and transparent operational queue reasons before taking any action.</p>
      </header>
      <WorkQueue
        error={queueError || undefined}
        items={items}
        onSelect={selectQueueItem}
        selectedNeedId={selected?.need_id}
        state={loadingQueue ? "loading" : undefined}
      />
      <PatientCase
        caseData={caseData}
        error={caseError || undefined}
        openNeeds={selected ? items.filter((item) => item.patient_id === selected.patient_id) : []}
        state={loadingCase ? "loading" : undefined}
      />
    </main>
  );
}

function readError(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

const mainStyle: CSSProperties = { background: "#f4f8f6", color: "#12302d", display: "grid", fontFamily: "Arial, sans-serif", gap: "1.5rem", margin: "0 auto", maxWidth: "76rem", minHeight: "100vh", padding: "clamp(1rem, 4vw, 2.5rem)" };
const eyebrow: CSSProperties = { color: "#075f5b", fontWeight: 700, letterSpacing: "0.04em", margin: 0, textTransform: "uppercase" };
