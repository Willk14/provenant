/** Matters, conflict checks, KYC, the file audit and the client trust account (ADR 0018). */
import { api } from "./api";

export interface PartyIn {
  name: string;
  role: "client" | "counterparty" | "related" | "beneficial_owner";
  identifier: string;
}

export interface Hit {
  party: string;
  party_role: string;
  matched: string;
  matched_role: string;
  source: string;
  score: number;
  kind: "adverse" | "same_side" | "screening";
  reason: string;
}

export interface Gap {
  item: string;
  label: string;
  problem: string;
  detail: string;
}

export interface MatterSummary {
  id: string;
  reference: string;
  title: string;
  matter_type: string;
  client_kind: "person" | "organisation";
  status: "conflict_review" | "open" | "declined" | "closed";
  responsible: string | null;
  created_by: string;
  created_at: string;
  possible_conflicts: number;
  gaps: number;
}

export interface Matter extends MatterSummary {
  parties: PartyIn[];
  conflict: {
    status: string;
    compared: number;
    hits: Hit[];
    run_by: string;
    run_at: string;
    decided_by: string | null;
    decision_note: string | null;
  } | null;
  requirements: {
    id: string;
    item: string;
    label: string;
    status: "provided" | "verified";
    filename: string;
    expires_on: string | null;
    provided_by: string;
    verified_by: string | null;
  }[];
  file_gaps: Gap[];
  events: { action: string; actor: string; note: string | null; at: string }[];
}

export interface Audit {
  as_of: string;
  requirements_version: string;
  matters: { id: string; reference: string; title: string; gaps: Gap[] }[];
  certificates: Gap[];
}

export interface TrustAccount {
  id: string;
  name: string;
  bank: string;
  dormant_days: number | null;
  dormant_basis: string | null;
  entries: number;
  cashbook: string;
}

export interface TrustEntry {
  id: string;
  date: string;
  amount: string;
  reference: string;
  description: string;
  matter: string | null;
  entered_by: string;
}

export interface Reconciliation {
  id: string;
  as_of: string;
  period_start: string;
  balanced: boolean;
  prepared_by: string;
  prepared_at: string;
  approved_by: string | null;
  approved_at: string | null;
  workpaper: {
    bank_balance: string;
    adjusted_bank: string;
    cashbook: string;
    ledgers_total: string;
    matched: number;
    outstanding: { date: string; amount: string; reference: string; matter: string | null }[];
    unrecorded: { date: string; amount: string; reference: string; description: string }[];
    ledgers: { matter: string; balance: string; last_movement: string }[];
    alerts: { kind: string; severity: "hard" | "warning"; message: string; amount: string; matter: string | null }[];
  };
}

const post = (body: unknown): RequestInit => ({
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

const form = (fields: Record<string, string | Blob>): RequestInit => {
  const data = new FormData();
  for (const [k, v] of Object.entries(fields)) data.append(k, v);
  return { method: "POST", body: data };
};

export const practiceApi = {
  matters: () => api.raw<MatterSummary[]>("/matters"),
  matter: (id: string) => api.raw<Matter>(`/matters/${id}`),
  openMatter: (body: { title: string; matter_type: string; client_kind: string; parties: PartyIn[] }) =>
    api.raw<Matter>("/matters", post(body)),
  rerunConflict: (id: string) => api.raw<Matter>(`/matters/${id}/conflict-check`, { method: "POST" }),
  decideConflict: (id: string, decision: "cleared" | "conflict", note: string) =>
    api.raw<Matter>(`/matters/${id}/conflict-decision`, post({ decision, note })),
  addRequirement: (id: string, item: string, file: File, expiresOn: string) =>
    api.raw<unknown>(`/matters/${id}/requirements`, form({ item, file, ...(expiresOn ? { expires_on: expiresOn } : {}) })),
  verifyRequirement: (id: string, requirementId: string) =>
    api.raw<unknown>(`/matters/${id}/requirements/${requirementId}/verify`, { method: "POST" }),
  requirementFile: (id: string, requirementId: string) =>
    api.raw<ArrayBuffer>(`/matters/${id}/requirements/${requirementId}/file`),
  audit: () => api.raw<Audit>("/practice/audit"),
  certificates: () => api.raw<{ holder: string; number: string; expires_on: string }[]>("/practice/certificates"),
  addCertificate: (holder: string, number: string, expiresOn: string) =>
    api.raw<unknown>("/practice/certificates", post({ holder, number, expires_on: expiresOn })),
  watchlists: () => api.raw<{ list_name: string; entries: number; uploaded_at: string }[]>("/practice/watchlists"),
  uploadWatchlist: (listName: string, file: File) =>
    api.raw<{ entries: number }>("/practice/watchlists", form({ list_name: listName, file })),
  trustAccounts: () => api.raw<TrustAccount[]>("/trust/accounts"),
  createTrustAccount: (body: { name: string; bank: string; dormant_days: number | null; dormant_basis: string | null }) =>
    api.raw<{ id: string }>("/trust/accounts", post(body)),
  trustEntries: (id: string) => api.raw<TrustEntry[]>(`/trust/accounts/${id}/entries`),
  addTrustEntry: (id: string, body: { entry_date: string; amount: string; reference: string; description: string; matter_id: string | null }) =>
    api.raw<unknown>(`/trust/accounts/${id}/entries`, post(body)),
  importCashbook: (id: string, file: File) =>
    api.raw<{ imported: number }>(`/trust/accounts/${id}/entries/import`, form({ file })),
  reconciliations: (id: string) => api.raw<Reconciliation[]>(`/trust/accounts/${id}/reconciliations`),
  reconcile: (id: string, asOf: string, bankBalance: string, statement: File) =>
    api.raw<Reconciliation>(`/trust/accounts/${id}/reconciliations`,
      form({ as_of: asOf, bank_balance: bankBalance, statement })),
  approveReconciliation: (id: string, reconciliationId: string) =>
    api.raw<Reconciliation>(`/trust/accounts/${id}/reconciliations/${reconciliationId}/approve`, { method: "POST" }),
};

export const STATUS_WORD: Record<MatterSummary["status"], string> = {
  conflict_review: "Conflict review", open: "Open", declined: "Declined", closed: "Closed",
};
export const STATUS_PILL: Record<MatterSummary["status"], string> = {
  conflict_review: "pill-warn", open: "pill-ok", declined: "pill-bad", closed: "",
};
