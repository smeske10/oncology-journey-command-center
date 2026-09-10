"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";

import { PatientCase } from "../../../components/navigator/patient-case";
import { NeedWorkspace } from "../../../components/navigator/need-workspace";
import { WorkQueue, type NavigatorQueueItem } from "../../../components/navigator/work-queue";
import {
  ApiError,
  bootstrapNavigatorQueue,
  getNavigatorNeedWorkspace,
  getNavigatorPatientCase,
  type NavigatorNeedWorkspaceResponse,
  type NavigatorPatientCaseResponse,
} from "../../../lib/api-client";

export default function NavigatorDemoPage() {
  const [items, setItems] = useState<NavigatorQueueItem[]>([]);
  const [queueError, setQueueError] = useState("");
  const [loadingQueue, setLoadingQueue] = useState(true);
  const [selected, setSelected] = useState<{ needId: string; patientId: string }>();
  const [caseData, setCaseData] = useState<NavigatorPatientCaseResponse>();
  const [caseError, setCaseError] = useState("");
  const [loadingCase, setLoadingCase] = useState(false);
  const [workspace, setWorkspace] = useState<NavigatorNeedWorkspaceResponse>();
  const [workspaceError, setWorkspaceError] = useState("");
  const requestId = useRef(0);
  const requestController = useRef<AbortController | undefined>(undefined);
  const selectedNeedId = selected?.needId;
  const selectedPatientId = selected?.patientId;

  const selectQueueItem = useCallback((item: NavigatorQueueItem) => {
    const selection = { needId: item.need_id, patientId: item.patient_id };
    setSelected(selection);
    window.localStorage.setItem("ojcc-navigator-selection", JSON.stringify(selection));
    setCaseData(undefined);
    setWorkspace(undefined);
    setCaseError("");
    setWorkspaceError("");
    setLoadingCase(true);
  }, []);

  const loadSelected = useCallback(async (needId: string, patientId: string) => {
    requestController.current?.abort();
    const controller = new AbortController();
    requestController.current = controller;
    const currentRequest = ++requestId.current;
    setLoadingCase(true);
    setCaseError("");
    setWorkspaceError("");
    const [caseResult, workspaceResult] = await Promise.allSettled([
      getNavigatorPatientCase(patientId, controller.signal),
      getNavigatorNeedWorkspace(needId, controller.signal),
    ]);
    if (controller.signal.aborted || requestId.current !== currentRequest) return;
    if (caseResult.status === "fulfilled") setCaseData(caseResult.value);
    else setCaseError(readError(caseResult.reason, "The patient case could not be loaded."));
    if (workspaceResult.status === "fulfilled") setWorkspace(workspaceResult.value);
    else setWorkspaceError(readError(workspaceResult.reason, "The selected need could not be loaded."));
    setLoadingCase(false);
  }, []);

  useEffect(() => {
    void bootstrapNavigatorQueue()
      .then((response) => {
        const queueItems = response.items;
        setItems(queueItems);
        const retained = readRetainedSelection();
        const retainedQueueItem = retained
          ? queueItems.find((item) => item.need_id === retained.needId && item.patient_id === retained.patientId)
          : undefined;
        if (retainedQueueItem) selectQueueItem(retainedQueueItem);
        else if (retained) setSelected(retained);
        else if (queueItems[0]) selectQueueItem(queueItems[0]);
      })
      .catch((error: unknown) => setQueueError(readError(error, "The navigator queue could not be loaded.")))
      .finally(() => setLoadingQueue(false));
  }, [selectQueueItem]);

  useEffect(() => {
    if (!selectedNeedId || !selectedPatientId) return;
    queueMicrotask(() => void loadSelected(selectedNeedId, selectedPatientId));
    return () => requestController.current?.abort();
  }, [loadSelected, selectedNeedId, selectedPatientId]);

  const refreshCanonical = useCallback(async () => {
    if (!selectedNeedId || !selectedPatientId) return;
    const response = await bootstrapNavigatorQueue();
    setItems(response.items);
    await loadSelected(selectedNeedId, selectedPatientId);
  }, [loadSelected, selectedNeedId, selectedPatientId]);

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
        selectedNeedId={selectedNeedId}
        state={loadingQueue ? "loading" : undefined}
      />
      <PatientCase
        caseData={caseData}
        error={caseError || undefined}
        openNeeds={
          caseData?.open_needs
          ?? (selectedPatientId ? items.filter((item) => item.patient_id === selectedPatientId) : [])
        }
        state={loadingCase ? "loading" : undefined}
      />
      <NeedWorkspace
        error={workspaceError || undefined}
        onRefresh={refreshCanonical}
        state={loadingCase ? "loading" : undefined}
        workspace={workspace}
      />
    </main>
  );
}

function readRetainedSelection(): { needId: string; patientId: string } | undefined {
  try {
    const parsed = JSON.parse(window.localStorage.getItem("ojcc-navigator-selection") ?? "null") as unknown;
    if (!parsed || typeof parsed !== "object") return undefined;
    const value = parsed as Record<string, unknown>;
    return typeof value.needId === "string" && typeof value.patientId === "string"
      ? { needId: value.needId, patientId: value.patientId }
      : undefined;
  } catch {
    return undefined;
  }
}

function readError(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

const mainStyle: CSSProperties = { background: "#f4f8f6", color: "#12302d", display: "grid", fontFamily: "Arial, sans-serif", gap: "1.5rem", margin: "0 auto", maxWidth: "76rem", minHeight: "100vh", padding: "clamp(1rem, 4vw, 2.5rem)" };
const eyebrow: CSSProperties = { color: "#075f5b", fontWeight: 700, letterSpacing: "0.04em", margin: 0, textTransform: "uppercase" };
