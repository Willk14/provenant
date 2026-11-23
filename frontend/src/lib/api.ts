import { identityHeaders } from "./identity";
import type { BBox, CovenantAdjustment, CovenantPeriodInput, CovenantRun, DocumentSummary, Field, FieldName, Schedule, TrialBalanceSummary } from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { ...identityHeaders(), ...(init.headers ?? {}) },
    cache: "no-store",
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      // non-JSON error body: keep the status text
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T; // e.g. linking a lease: nothing to read back
  const type = response.headers.get("content-type") ?? "";
  return (type.includes("application/json") ? response.json() : response.arrayBuffer()) as Promise<T>;
}

function json(body: unknown): RequestInit {
  return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export const api = {
  /** Any endpoint, with identity headers and error handling (used by feature modules like lib/tax). */
  raw: <T,>(path: string, init: RequestInit = {}) => call<T>(path, init),
  documents: () => call<DocumentSummary[]>("/documents"),
  document: (id: string) => call<DocumentSummary>(`/documents/${id}`),
  pdf: (id: string) => call<ArrayBuffer>(`/documents/${id}/file`),
  /** With `amends`, the file is an amendment to that lease. */
  upload: (file: File, amends?: string, kind: "lease" | "credit_agreement" = "lease") => {
    const form = new FormData();
    form.append("file", file);
    if (amends) form.append("amends", amends);
    else form.append("kind", kind);
    return call<DocumentSummary>("/documents", { method: "POST", body: form });
  },
  reviewQueue: () => call<Field[]>("/review-queue"),
  review: (fieldId: string, action: "approved" | "corrected" | "rejected", correctedValue?: string) =>
    call<Field>(
      `/fields/${fieldId}/review`,
      json(action === "corrected" ? { action, corrected_value: correctedValue } : { action }),
    ),
  enterField: (documentId: string, name: FieldName, value: string, page: number, bbox: BBox) =>
    call<Field>(`/documents/${documentId}/fields`, json({ name, value, page, bbox })),
  enterInput: (documentId: string, name: FieldName, value: string, basis: string) =>
    call<Field>(`/documents/${documentId}/inputs`, json({ name, value, basis })),
  schedule: (id: string) => call<Schedule>(`/documents/${id}/lease-schedule`),
  workbook: (id: string) => call<ArrayBuffer>(`/documents/${id}/lease-schedule.xlsx`),
  trialBalances: (id: string) => call<TrialBalanceSummary[]>(`/documents/${id}/trial-balances`),
  uploadTrialBalance: (id: string, file: File, periodEnd: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("period_end", periodEnd);
    return call<TrialBalanceSummary>(`/documents/${id}/trial-balances`, { method: "POST", body: form });
  },
  deleteTrialBalance: (tbId: string) => call<ArrayBuffer>(`/trial-balances/${tbId}`, { method: "DELETE" }),
  covenants: (id: string, asOf?: string) =>
    call<CovenantRun>(`/documents/${id}/covenants${asOf ? `?as_of=${asOf}` : ""}`),
  savePeriodInput: (id: string, periodEnd: string, body: { revolver_drawn: string | null; delivered_on: string | null; basis: string }) =>
    call<CovenantPeriodInput>(`/documents/${id}/covenants/periods/${periodEnd}`, { ...json(body), method: "PUT" }),
  confirmPeriodInput: (id: string, periodEnd: string) =>
    call<CovenantPeriodInput>(`/documents/${id}/covenants/periods/${periodEnd}/confirm`, { method: "POST" }),
  addCovenantAdjustment: (id: string, body: {
    kind: CovenantAdjustment["kind"]; clause: string; basis: string; quarter_end?: string; label?: string;
    amount?: string; capped?: boolean; lease_document_id?: string;
  }) => call<CovenantAdjustment>(`/documents/${id}/covenants/adjustments`, json(body)),
  approveCovenantAdjustment: (id: string, adjustmentId: string) =>
    call<CovenantAdjustment>(`/documents/${id}/covenants/adjustments/${adjustmentId}/approve`, { method: "POST" }),
  deleteCovenantAdjustment: (id: string, adjustmentId: string) =>
    call<void>(`/documents/${id}/covenants/adjustments/${adjustmentId}`, { method: "DELETE" }),
  verifyLog: () => call<{ intact: boolean; events: number }>("/review-log/verify"),
};
