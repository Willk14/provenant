/** Phase 2 practice agents: precedents, docketing and due diligence (ADR 0019). */
import { api } from "./api";

export interface Precedent {
  id: string;
  name: string;
  kind: string;
  version: number;
  body: string;
  fields: string[];
  options: string[];
  locked_by: string;
  locked_at: string;
}

export interface PrecedentDraft {
  id: string;
  precedent: string;
  version: number;
  status: "ready" | "returned" | "approved";
  text: string;
  unfilled: string[];
  deviations: { kind: "changed" | "added" | "removed"; standard: string; edited: string }[];
  values: Record<string, string>;
  created_by: string;
  edited_by: string | null;
  decided_by: string | null;
  decision_note: string | null;
  updated_at: string;
}

export interface DeadlineRule {
  id: string;
  name: string;
  trigger: string;
  days: number;
  months: number;
  counting: "calendar" | "court_days";
  roll_forward: boolean;
  basis: string;
  describe: string;
}

export interface DocketEntry {
  id: string;
  matter_id: string;
  matter: string;
  title: string;
  due_on: string;
  basis: string;
  status: "open" | "done";
  done_by: string | null;
}

export interface CourtNotice {
  id: string;
  status: "new" | "confirmed" | "dismissed";
  text: string;
  reading: {
    case_number: string | null;
    event: string | null;
    dates: string[];
    evidence: Record<string, string>;
    reader?: "claude" | "rules";
  };
  event: string | null;
  event_date: string | null;
  case_number: string | null;
  created_by: string;
  confirmed_by: string | null;
}

export interface DiligenceCell {
  state: "finding" | "not_found" | "not_reviewed" | "reviewing" | "failed";
  severity: "high" | "medium" | "low" | "info" | null;
  findings: number;
  title: string;
  quote: string;
  page: number | null;
  deviation: boolean;
}

export interface Diligence {
  matter: string;
  topics: { key: string; label: string }[];
  rows: { document_id: string; filename: string; deviations: number; cells: Record<string, DiligenceCell> }[];
  documents: number;
  reviewed: number;
}

const post = (body: unknown, method = "POST"): RequestInit => ({
  method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

export const phase2Api = {
  precedents: () => api.raw<Precedent[]>("/precedents"),
  createPrecedent: (name: string, kind: string, body: string) => api.raw<Precedent>("/precedents", post({ name, kind, body })),
  newVersion: (id: string, body: string) => api.raw<Precedent>(`/precedents/${id}/versions`, post({ body })),
  drafts: (matterId: string) =>
    api.raw<{ facts: Record<string, string>; drafts: PrecedentDraft[] }>(`/matters/${matterId}/precedent-drafts`),
  createDraft: (matterId: string, precedentId: string, values: Record<string, string>, include: string[]) =>
    api.raw<PrecedentDraft>(`/matters/${matterId}/precedent-drafts`, post({ precedent_id: precedentId, values, include })),
  editDraft: (matterId: string, draftId: string, text: string) =>
    api.raw<PrecedentDraft>(`/matters/${matterId}/precedent-drafts/${draftId}`, post({ text }, "PUT")),
  decideDraft: (matterId: string, draftId: string, decision: "approved" | "returned", note: string) =>
    api.raw<PrecedentDraft>(`/matters/${matterId}/precedent-drafts/${draftId}/decision`, post({ decision, note })),
  rules: () => api.raw<DeadlineRule[]>("/docket/rules"),
  addRule: (body: Omit<DeadlineRule, "id" | "describe">) => api.raw<unknown>("/docket/rules", post(body)),
  retireRule: (id: string) => api.raw<unknown>(`/docket/rules/${id}/retire`, { method: "POST" }),
  holidays: () => api.raw<{ id: string; day: string; name: string }[]>("/docket/holidays"),
  addHoliday: (day: string, name: string) => api.raw<unknown>("/docket/holidays", post({ day, name })),
  docket: () => api.raw<DocketEntry[]>("/docket"),
  calendar: () => api.raw<ArrayBuffer>("/docket.ics"),
  markDone: (id: string) => api.raw<unknown>(`/docket/${id}/done`, { method: "POST" }),
  matterDocket: (matterId: string) =>
    api.raw<{ notices: CourtNotice[]; entries: DocketEntry[] }>(`/matters/${matterId}/docket`),
  addNotice: (matterId: string, text: string) => api.raw<CourtNotice>(`/matters/${matterId}/notices`, post({ text })),
  confirmNotice: (matterId: string, noticeId: string, event: string, eventDate: string, caseNumber: string) =>
    api.raw<unknown>(`/matters/${matterId}/notices/${noticeId}/confirm`,
      post({ event, event_date: eventDate, case_number: caseNumber })),
  addDeadline: (matterId: string, title: string, dueOn: string, basis: string) =>
    api.raw<unknown>(`/matters/${matterId}/docket`, post({ title, due_on: dueOn, basis })),
  linked: (matterId: string) =>
    api.raw<{ id: string; filename: string; kind: string; status: string }[]>(`/matters/${matterId}/documents`),
  link: (matterId: string, documentIds: string[]) =>
    api.raw<{ added: number }>(`/matters/${matterId}/documents`, post({ document_ids: documentIds })),
  reviewAll: (matterId: string) =>
    api.raw<{ started: number; skipped: { filename: string; reason: string }[] }>(`/matters/${matterId}/due-diligence/review`,
      { method: "POST" }),
  diligence: (matterId: string) => api.raw<Diligence>(`/matters/${matterId}/due-diligence`),
  diligenceXlsx: (matterId: string) => api.raw<ArrayBuffer>(`/matters/${matterId}/due-diligence.xlsx`),
};

export function saveBytes(bytes: ArrayBuffer, filename: string, type: string): void {
  const url = URL.createObjectURL(new Blob([bytes], { type }));
  Object.assign(document.createElement("a"), { href: url, download: filename }).click();
  URL.revokeObjectURL(url);
}

/** Claude in the practice agents (ADR 0020): each answer is checked against its source before it reaches you. */
export interface FieldSuggestion { field: string; value: string; source: string; quote: string }
export interface Explanation {
  entries: { id: string; date: string; amount: string; reference: string; matter: string | null }[];
  bank_lines: { date: string; amount: string; reference: string; description: string }[];
  difference: string;
  likely_cause: string;
  next_step: string;
}

export const claudeApi = {
  suggestValues: (matterId: string, precedentId: string, values: Record<string, string>) =>
    api.raw<{ empty: string[]; suggestions: FieldSuggestion[]; documents: number }>(
      `/matters/${matterId}/precedent-drafts/suggest`, post({ precedent_id: precedentId, values })),
  kycRequest: (matterId: string) =>
    api.raw<{ letter: string; asks_for: string[] }>(`/matters/${matterId}/kyc-request`, { method: "POST" }),
  explainReconciliation: (accountId: string, reconciliationId: string) =>
    api.raw<{ explanations: Explanation[] }>(
      `/trust/accounts/${accountId}/reconciliations/${reconciliationId}/explain`, { method: "POST" }),
};
