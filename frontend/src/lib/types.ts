export type FieldName =
  | "commencement_date"
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

export interface DocumentSummary {
  id: string;
  sha256: string;
  filename: string;
  size_bytes: number;
  uploaded_by: string;
  status: DocumentStatus;
  error: string | null;
  created_at: string;
  needs_review: number | null;
  missing_terms: number | null;
  fields: Field[];
}

export interface Period {
  number: number;
  end_date: string;
  opening: string;
  interest: string;
  payment: string;
  closing: string;
  depreciation: string;
}

export interface Schedule {
  initial_liability: string;
  initial_rou_asset: string;
  periods: Period[];
  inputs: Field[];
}

export interface Me {
  tenant_id: string;
  user: string;
  roles: string[];
}

export const FIELD_ORDER: FieldName[] = [
  "commencement_date",
  "term_months",
  "monthly_payment",
  "payment_timing",
  "annual_discount_rate",
];

export const FIELD_LABELS: Record<FieldName, string> = {
  commencement_date: "Commencement date",
  term_months: "Lease term (months)",
  monthly_payment: "Monthly payment",
  payment_timing: "Payment timing",
  annual_discount_rate: "Discount rate (annual)",
};

/** Mirrors LEASE_GATE.always_review on the server: these need a partner's sign-off. */
export const JUDGEMENT_FIELDS: ReadonlySet<FieldName> = new Set(["annual_discount_rate", "payment_timing"]);
