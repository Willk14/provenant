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
  disposals?: string;
  asset_opening_values?: Record<string, string>;
  business_ceased?: boolean;
}

export type CreditKind = "instalment" | "withholding" | "overpayment" | "other";

export interface Credit {
  kind: CreditKind;
  description: string;
  amount: string;
  reference: string;
  evidence: string | null;
  paid_on?: string | null;
}

export interface Inputs {
  losses_brought_forward: string;
  allowances: Allowance[];
  credits: Credit[];
  prior_return: { reference: string; filed_on: string; assessed_tax: string; agriculture_income_ratio: string };
  updated_by?: string | null;
}

export interface ClassYear {
  asset_class: string;
  additions: string;
  disposals: string;
  cost_held: string;
  assets_added: string[];
  assets_disposed: string[];
  assets_held: number;
}

export interface Register {
  id: string;
  filename: string;
  sha256: string;
  uploaded_by: string;
  classes: ClassYear[];
  assets: {
    code: string;
    description: string;
    asset_class: string;
    acquired: string;
    cost: string;
    disposed: string | null;
    proceeds: string;
  }[];
}

export interface Attachment {
  id: string;
  year_end: string;
  category: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  account_code: string | null;
  note: string;
  uploaded_by: string;
  created_at: string;
}

export interface RecordsMeta {
  credit_kinds: Record<CreditKind, string>;
  attachment_categories: Record<string, string>;
}

export interface CompLine {
  label: string;
  amount: string;
  basis: string;
  sources: string[];
}

export interface Computation {
  approval: { reviewer: string; approved_at: string } | null;
  changed_since_approval: boolean;
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
  credits: CompLine[];
  tax_due: string;
  instalment_plan: {
    annual_amount: string;
    basis_used: string;
    prior_year_basis: string | null;
    current_year_basis: string;
    withholding_reduction: string;
    agriculture_income_ratio: string;
    balance_due_date: string;
    balance_due_basis: string;
    balance_due_amount: string;
    unallocated_instalment_payments: string;
    installments: { number: number; due_date: string; proportion: string; amount: string; paid_to_date: string;
      outstanding: string; basis: string; sources: string[] }[];
  } | null;
  credit_items: (Credit & { evidence_filename: string | null })[];
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
  allowances: {
    name: string;
    wdv_brought_forward: string;
    additions: string;
    disposals: string;
    rate: string;
    method: string;
    cost_basis: string;
    allowance: string;
    disposal_gain: string;
    balancing_charge: string;
    balancing_deduction: string;
    business_ceased: boolean;
    wdv_carried_forward: string;
    basis: string;
    from_register: boolean;
    asset_schedule: {
      code: string;
      opening_wdv: string;
      additions: string;
      allowance: string;
      proceeds: string;
      disposal_result: string | null;
      closing_wdv: string;
      rate_basis: string;
      disposal_basis: string;
      source: string;
    }[];
  }[];
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

const MEDIA_TYPES: Record<string, string> = {
  pdf: "application/pdf",
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  csv: "text/csv",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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
  approveComputation: (id: string, yearEnd: string) =>
    api.raw<{ reviewer: string; approved_at: string }>(`${base(id, yearEnd)}/tax/approval`, { method: "POST" }),
  exportComputation: (id: string, yearEnd: string) =>
    api.raw<ArrayBuffer>(`${base(id, yearEnd)}/tax.xlsx`),
  rollForward: (id: string, yearEnd: string) =>
    api.raw<Inputs>(`${base(id, yearEnd)}/inputs/roll-forward`, { method: "POST" }),
  meta: () => api.raw<RecordsMeta>("/tax-records/meta"),
  register: (id: string, yearEnd: string) => api.raw<Register | null>(`${base(id, yearEnd)}/fixed-assets`),
  uploadRegister: (id: string, yearEnd: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return api.raw<Register>(`${base(id, yearEnd)}/fixed-assets`, { method: "POST", body: form });
  },
  attachments: (id: string, yearEnd: string) => api.raw<Attachment[]>(`${base(id, yearEnd)}/attachments`),
  uploadAttachment: (id: string, yearEnd: string, file: File, category: string, accountCode: string, note: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("category", category);
    form.append("account_code", accountCode);
    form.append("note", note);
    return api.raw<Attachment>(`${base(id, yearEnd)}/attachments`, { method: "POST", body: form });
  },
  deleteAttachment: (id: string, attachmentId: string) =>
    api.raw<unknown>(`/clients/${id}/attachments/${attachmentId}`, { method: "DELETE" }),
  /** Fetch with identity headers and open in a new tab (a plain link would not carry them). */
  openAttachment: async (id: string, attachmentId: string, filename: string) => {
    const data = await api.raw<ArrayBuffer>(`/clients/${id}/attachments/${attachmentId}/file`);
    const ext = filename.split(".").pop()?.toLowerCase() ?? "";
    const url = URL.createObjectURL(new Blob([data], { type: MEDIA_TYPES[ext] ?? "application/octet-stream" }));
    window.open(url, "_blank", "noopener");
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  },
  linkLease: (documentId: string, clientId: string | null) =>
    api.raw<unknown>(`/documents/${documentId}/client`, json("PUT", { client_id: clientId })),
};
