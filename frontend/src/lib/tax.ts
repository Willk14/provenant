import { api } from "./api";

export interface Client {
  id: string;
  name: string;
  created_at: string;
  year_ends: string[];
}

export interface Account {
  line_id: string;
  code: string;
  name: string;
  balance: string;
  fs_line: string | null;
  tax_treatment: string | null;
  status: "unmapped" | "suggested" | "confirmed";
  rule: string | null;
  confirmed_by: string | null;
}

export interface LeadLine {
  fs_line: string;
  label: string;
  statement: "position" | "profit_or_loss";
  amount: string;
  codes: string[];
}

export interface Mapping {
  trial_balance: { id: string; year_end: string; filename: string; sha256: string; uploaded_by: string };
  accounts: Account[];
  lead_schedule: LeadLine[];
  fs_lines: Record<string, { label: string; statement: "position" | "profit_or_loss"; default: string }>;
  tax_treatments: string[];
}

export interface Allowance {
  name: string;
  wdv_brought_forward: string;
  additions: string;
  rate_percent: string;
  method: "reducing" | "straight_line";
  basis: string;
  cost_basis?: string;
}

export interface Inputs {
  losses_brought_forward: string;
  allowances: Allowance[];
  updated_by?: string | null;
}

export interface CompLine {
  label: string;
  amount: string;
  basis: string;
  sources: string[];
}

export interface Computation {
  rules_version: string;
  profit_before_tax: string;
  adjustments: CompLine[];
  taxable_before_losses: string;
  losses_brought_forward: string;
  losses_used: string;
  losses_carried_forward: string;
  taxable_profit: string;
  rate: string;
  tax_payable: string;
  deferred: CompLine[];
  deferred_tax: string;
  warnings: string[];
  leases: {
    document_id: string;
    name: string;
    payments: string;
    rou_depreciation: string;
    interest: string;
    rou_carrying_amount: string;
    liability: string;
  }[];
  allowances: { name: string; allowance: string; wdv_carried_forward: string; basis: string }[];
  accounts: Record<string, string>;
}

export const TREATMENT_LABELS: Record<string, string> = {
  taxable: "Taxable income",
  exempt: "Exempt income",
  deductible: "Deductible",
  disallowable: "Disallowable (add back)",
  depreciation: "Depreciation (add back)",
  lease_interest: "IFRS 16 lease interest",
  balance_sheet: "Balance sheet",
  tax_charge: "Tax charge (excluded)",
};

function json(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

const base = (id: string, yearEnd: string) => `/clients/${id}/years/${yearEnd}`;

export const taxApi = {
  clients: () => api.raw<Client[]>("/clients"),
  createClient: (name: string) => api.raw<Client>("/clients", json("POST", { name })),
  uploadTrialBalance: (id: string, yearEnd: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    form.append("year_end", yearEnd);
    return api.raw<unknown>(`/clients/${id}/trial-balances`, { method: "POST", body: form });
  },
  mapping: (id: string, yearEnd: string) => api.raw<Mapping>(`${base(id, yearEnd)}/mapping`),
  mapAccount: (id: string, code: string, fsLine: string, treatment: string) =>
    api.raw<Account>(`/clients/${id}/mappings/${encodeURIComponent(code)}`, json("PUT", { fs_line: fsLine, tax_treatment: treatment })),
  confirmSuggestions: (id: string, yearEnd: string) =>
    api.raw<{ confirmed: number }>(`${base(id, yearEnd)}/confirm-suggestions`, { method: "POST" }),
  inputs: (id: string, yearEnd: string) => api.raw<Inputs>(`${base(id, yearEnd)}/inputs`),
  saveInputs: (id: string, yearEnd: string, inputs: Inputs) =>
    api.raw<Inputs>(`${base(id, yearEnd)}/inputs`, json("PUT", inputs)),
  computation: (id: string, yearEnd: string) => api.raw<Computation>(`${base(id, yearEnd)}/tax`),
  linkLease: (documentId: string, clientId: string | null) =>
    api.raw<unknown>(`/documents/${documentId}/client`, json("PUT", { client_id: clientId })),
};
