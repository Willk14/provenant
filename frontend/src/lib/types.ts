export type FieldName =
  | "commencement_date"
  | "effective_date"
  | "term_months"
  | "monthly_payment"
  | "annual_discount_rate"
  | "payment_timing"
  | "rent_steps"
  | "free_periods"
  | "max_leverage_ratio"
  | "min_interest_cover"
  | "min_liquidity"
  | "first_test_date"
  | "reporting_days"
  | "cure_period_days";

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
  /** Set for a preparer input (page 0): a value the document does not state, with how it was determined. */
  basis?: string | null;
}

/** Terms a preparer may enter with a basis instead of a location: contracts rarely state them (ADR 0005). */
export const INPUT_FIELDS: ReadonlySet<FieldName> = new Set<FieldName>(["annual_discount_rate"]);

export type DocumentStatus = "queued" | "extracting" | "extracted" | "failed";
export type DocumentKind = "lease" | "amendment" | "credit_agreement";

export interface DocumentSummary {
  id: string;
  sha256: string;
  filename: string;
  size_bytes: number;
  uploaded_by: string;
  kind: DocumentKind;
  parent_id: string | null;
  parent_sha256: string | null;
  client_id?: string | null;
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
    order: [
      "commencement_date",
      "term_months",
      "monthly_payment",
      "rent_steps",
      "free_periods",
      "payment_timing",
      "annual_discount_rate",
    ],
    required: new Set(["commencement_date", "term_months", "monthly_payment", "payment_timing", "annual_discount_rate"]),
    judgement: new Set(["annual_discount_rate", "payment_timing"]),
    labels: {},
  },
  amendment: {
    order: ["effective_date", "monthly_payment", "rent_steps", "free_periods", "term_months", "annual_discount_rate"],
    required: new Set(["effective_date", "annual_discount_rate"]),
    judgement: new Set(["effective_date", "annual_discount_rate"]),
    labels: {
      monthly_payment: "New monthly payment",
      term_months: "New total term (months)",
      rent_steps: "New rent steps",
      annual_discount_rate: "Revised discount rate (annual)",
    },
  },
  credit_agreement: {
    order: [
      "max_leverage_ratio",
      "min_interest_cover",
      "min_liquidity",
      "first_test_date",
      "reporting_days",
      "cure_period_days",
    ],
    required: new Set(["first_test_date", "reporting_days"]),
    judgement: new Set(["max_leverage_ratio", "min_interest_cover", "min_liquidity", "cure_period_days"]),
    labels: {},
  },
};

export const FIELD_LABELS: Record<FieldName, string> = {
  commencement_date: "Commencement date",
  effective_date: "Effective date",
  term_months: "Lease term (months)",
  monthly_payment: "Monthly payment",
  payment_timing: "Payment timing",
  rent_steps: "Rent steps (escalations)",
  free_periods: "Rent-free months",
  annual_discount_rate: "Discount rate (annual)",
  max_leverage_ratio: "Maximum leverage (net debt / EBITDA)",
  min_interest_cover: "Minimum interest cover (EBITDA / interest)",
  min_liquidity: "Minimum liquidity (cash)",
  first_test_date: "First test date",
  reporting_days: "Compliance certificate due (days after quarter-end)",
  cure_period_days: "Equity cure window (days)",
};

export type CovenantKind = "leverage" | "interest_cover" | "min_liquidity";

export const COVENANT_LABELS: Record<CovenantKind, string> = {
  leverage: "Leverage",
  interest_cover: "Interest cover",
  min_liquidity: "Liquidity",
};

export const COVENANT_FIELD: Record<CovenantKind, FieldName> = {
  leverage: "max_leverage_ratio",
  interest_cover: "min_interest_cover",
  min_liquidity: "min_liquidity",
};

export interface TrialBalanceSummary {
  id: string;
  period_end: string;
  filename: string;
  sha256: string;
  uploaded_by: string;
  lines: number;
}

export interface CovenantResult {
  kind: CovenantKind;
  field_id: string;
  threshold: string;
  status: "pass" | "warning" | "breach";
  value: string | null;
  headroom: string | null;
  workings: { label: string; amount: string }[];
  note: string;
}

export interface CovenantTestDate {
  period_end: string;
  certificate_due: string;
  cure_deadline: string | null;
  status: "tested" | "awaiting" | "overdue";
  missing: string | null;
  trial_balance_ids: string[];
  results: CovenantResult[];
}

export interface CovenantAlert {
  severity: "breach" | "overdue" | "warning" | "due";
  on: string;
  message: string;
}

export interface CovenantRun {
  as_of: string;
  terms: { first_test_date: string; reporting_days: number; cure_days: number | null; warning_headroom: string };
  test_dates: CovenantTestDate[];
  alerts: CovenantAlert[];
  inputs: Field[];
}

export function fieldLabel(name: FieldName, kind: DocumentKind = "lease"): string {
  return KINDS[kind].labels[name] ?? FIELD_LABELS[name];
}

/** The lease's terms, in display order (kept for callers that only deal with leases). */
export const FIELD_ORDER: FieldName[] = KINDS.lease.order;
