export type FieldName =
  | "commencement_date"
  | "effective_date"
  | "term_months"
  | "monthly_payment"
  | "annual_discount_rate"
  | "payment_timing";

export type FieldStatus = "proposed" | "auto_accepted" | "needs_review" | "approved" | "corrected" | "rejected";

/** (x0, top, x1, bottom) in PDF points, origin at the page's top-left. */
export type BBox = [number, number, number, number];

export interface Field {
  id: string;
  document_id: string;
  name: FieldName;
  value: string;
  confidence: number;
  status: FieldStatus;
  gate_version: string | null;
  extractor: string;
  entered_by: string | null;
  page: number;
  bbox: BBox;
  char_start: number;
  char_end: number;
}

export type DocumentStatus = "queued" | "extracting" | "extracted" | "failed";
export type DocumentKind = "lease" | "amendment";

export interface DocumentSummary {
  id: string;
  sha256: string;
  filename: string;
  size_bytes: number;
  uploaded_by: string;
  kind: DocumentKind;
  parent_id: string | null;
  parent_sha256: string | null;
  status: DocumentStatus;
  error: string | null;
  created_at: string;
  needs_review: number | null;
  missing_terms: number | null;
  fields: Field[];
  amendments: DocumentSummary[];
  parent: DocumentSummary | null;
}

export interface Period {
  number: number;
  end_date: string;
  opening: string;
  interest: string;
  payment: string;
  closing: string;
  depreciation: string;
  segment: number;
}

export interface Remeasurement {
  after_period: number;
  effective: string;
  document_id: string;
  filename: string;
  liability_before: string;
  liability_after: string;
  rou_before: string;
  rou_adjustment: string;
  gain: string;
}

export interface Segment {
  document_id: string;
  from_period: number;
  inputs: Partial<Record<FieldName, string>>; // term -> field id
}

export interface Schedule {
  initial_liability: string;
  initial_rou_asset: string;
  periods: Period[];
  remeasurements: Remeasurement[];
  segments: Segment[];
  chain_fingerprint: string;
  inputs: Field[];
}

export interface Me {
  tenant_id: string;
  user: string;
  roles: string[];
}

export interface KindSpec {
  order: FieldName[];
  required: ReadonlySet<FieldName>;
  /** Mirrors the server's gate always_review for this kind: these need a partner's sign-off. */
  judgement: ReadonlySet<FieldName>;
  labels: Partial<Record<FieldName, string>>;
}

export const KINDS: Record<DocumentKind, KindSpec> = {
  lease: {
    order: ["commencement_date", "term_months", "monthly_payment", "payment_timing", "annual_discount_rate"],
    required: new Set(["commencement_date", "term_months", "monthly_payment", "payment_timing", "annual_discount_rate"]),
    judgement: new Set(["annual_discount_rate", "payment_timing"]),
    labels: {},
  },
  amendment: {
    order: ["effective_date", "monthly_payment", "term_months", "annual_discount_rate"],
    required: new Set(["effective_date", "annual_discount_rate"]),
    judgement: new Set(["effective_date", "annual_discount_rate"]),
    labels: {
      monthly_payment: "New monthly payment",
      term_months: "New total term (months)",
      annual_discount_rate: "Revised discount rate (annual)",
    },
  },
};

export const FIELD_LABELS: Record<FieldName, string> = {
  commencement_date: "Commencement date",
  effective_date: "Effective date",
  term_months: "Lease term (months)",
  monthly_payment: "Monthly payment",
  payment_timing: "Payment timing",
  annual_discount_rate: "Discount rate (annual)",
};

export function fieldLabel(name: FieldName, kind: DocumentKind = "lease"): string {
  return KINDS[kind].labels[name] ?? FIELD_LABELS[name];
}

/** The lease's terms, in display order (kept for callers that only deal with leases). */
export const FIELD_ORDER: FieldName[] = KINDS.lease.order;
