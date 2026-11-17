import { identityHeaders } from "./identity";
import type { BBox, DocumentSummary, Field, FieldName, Schedule } from "./types";

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
  const type = response.headers.get("content-type") ?? "";
  return (type.includes("application/json") ? response.json() : response.arrayBuffer()) as Promise<T>;
}

function json(body: unknown): RequestInit {
  return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export const api = {
  documents: () => call<DocumentSummary[]>("/documents"),
  document: (id: string) => call<DocumentSummary>(`/documents/${id}`),
  pdf: (id: string) => call<ArrayBuffer>(`/documents/${id}/file`),
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
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
  schedule: (id: string) => call<Schedule>(`/documents/${id}/lease-schedule`),
  workbook: (id: string) => call<ArrayBuffer>(`/documents/${id}/lease-schedule.xlsx`),
  verifyLog: () => call<{ intact: boolean; events: number }>("/review-log/verify"),
};
