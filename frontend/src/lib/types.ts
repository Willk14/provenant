export type FieldName =
  | "commencement_date"
  | "effective_date"
  | "terms_start_date"
  | "expiry_date"
  | "expansion_treatment"
  | "term_months"
  | "monthly_payment"
  | "annual_discount_rate"
  | "payment_timing"
  | "rent_steps"
  | "free_periods"
  | "max_leverage_ratio"
  | "min_interest_cover"
  | "min_liquidity"
  | "max_leverage_steps"
  | "min_interest_cover_steps"
  | "min_liquidity_steps"
  | "first_test_date"
  | "reporting_days"
  | "cure_period_days"
  | "cure_day_basis"
  | "cure_runs_from"
  | "springing_usage_percent"
  | "revolver_commitment"
  | "addback_cap_percent"
  | "addback_cap_basis"
  | "lease_treatment";

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
export const INPUT_FIELDS: ReadonlySet<FieldName> = new Set<FieldName>([
  "annual_discount_rate", "expansion_treatment", "cure_day_basis", "cure_runs_from", "springing_usage_percent",
  "revolver_commitment", "addback_cap_percent", "addback_cap_basis", "lease_treatment",
]);
/** Judgements no contract states: entered as inputs only, never marked on the PDF. */
export const INPUT_ONLY_FIELDS: ReadonlySet<FieldName> = new Set<FieldName>(["expansion_treatment"]);

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
  /** The period boundary the remeasurement is posted at (on or after the agreement date). */
  remeasured_on?: string | null;
  /** When the new rent or term starts. */
  terms_start?: string | null;
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
  /** Added space accounted for as its own lease (IFRS 16.44), one per expansion amendment. */
  separate_leases?: SeparateLease[];
}

export interface SeparateLease {
  document_id: string;
  filename: string;
  commencement: string;
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
    order: ["effective_date", "terms_start_date", "expansion_treatment", "monthly_payment", "rent_steps",
      "free_periods", "expiry_date", "term_months", "annual_discount_rate"],
    required: new Set(["effective_date", "annual_discount_rate"]),
    judgement: new Set(["effective_date", "annual_discount_rate", "expansion_treatment"]),
    labels: {
      effective_date: "Agreed (IFRS 16 effective) date",
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
      "max_leverage_steps",
      "min_interest_cover_steps",
      "min_liquidity_steps",
      "first_test_date",
      "reporting_days",
      "cure_period_days",
      "cure_day_basis",
      "cure_runs_from",
      "springing_usage_percent",
      "revolver_commitment",
      "addback_cap_percent",
      "addback_cap_basis",
      "lease_treatment",
    ],
    required: new Set(["first_test_date", "reporting_days"]),
    judgement: new Set([
      "max_leverage_ratio", "min_interest_cover", "min_liquidity", "max_leverage_steps", "min_interest_cover_steps",
      "min_liquidity_steps", "cure_period_days", "cure_day_basis", "cure_runs_from", "springing_usage_percent", "revolver_commitment",
      "addback_cap_percent", "addback_cap_basis", "lease_treatment",
    ]),
    labels: {},
  },
};

export const FIELD_LABELS: Record<FieldName, string> = {
  commencement_date: "Commencement date",
  effective_date: "Effective date",
  terms_start_date: "New terms start",
  expiry_date: "New expiry date",
  expansion_treatment: "Expansion (IFRS 16.44)",
  term_months: "Lease term (months)",
  monthly_payment: "Monthly payment",
  payment_timing: "Payment timing",
  rent_steps: "Rent steps (escalations)",
  free_periods: "Rent-free months",
  annual_discount_rate: "Discount rate (annual)",
  max_leverage_ratio: "Maximum leverage (net debt / EBITDA)",
  min_interest_cover: "Minimum interest cover (EBITDA / interest)",
  min_liquidity: "Minimum liquidity (cash)",
  max_leverage_steps: "Leverage threshold changes",
  min_interest_cover_steps: "Interest cover threshold changes",
  min_liquidity_steps: "Liquidity threshold changes",
  first_test_date: "First test date",
  reporting_days: "Compliance certificate due (days after quarter-end)",
  cure_period_days: "Equity cure window (days)",
  cure_day_basis: "Cure days counted as",
  cure_runs_from: "Cure window runs from",
  springing_usage_percent: "Leverage tested above revolver usage of",
  revolver_commitment: "Revolver commitment",
  addback_cap_percent: "EBITDA add-back cap",
  addback_cap_basis: "Add-back cap measured",
  lease_treatment: "Lease treatment in covenants",
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

export interface LegalCitation {
  document_id: string;
  filename: string;
  quote: string;
  page: number;
  bbox: BBox;
}

/** A cited issue from the review agent. It means nothing until a person accepts it. */
export interface LegalFinding {
  id: string;
  topic: string;
  severity: "high" | "medium" | "low" | "info";
  title: string;
  explanation: string;
  suggestion: string;
  citation: LegalCitation;
  proposed_term: FieldName | null;
  proposed_value: string | null;
  decision: "accepted" | "rejected" | null;
  decision_note: string | null;
  decided_by: string | null;
  field_id: string | null;
}

export interface LegalQuestion {
  id: string;
  question: string;
  status: "queued" | "running" | "done" | "failed";
  answer: string | null;
  not_stated: boolean;
  citations: LegalCitation[];
  error: string | null;
  asked_by: string;
  created_at: string;
}

export interface LegalReviewSummary {
  id: string;
  status: "queued" | "running" | "done" | "failed";
  agent: string | null;
  playbook_version: string;
  playbook_sha256: string;
  summary: string | null;
  not_found: string[];
  error: string | null;
  requested_by: string;
  created_at: string;
  finished_at: string | null;
}

export interface LegalReview extends LegalReviewSummary {
  findings: LegalFinding[];
  questions: LegalQuestion[];
}

export interface LegalPlaybook {
  version: string;
  sha256: string;
  updated_by: string | null;
  topics: { key: string; label: string; position: string; standard: boolean }[];
}

export interface LegalIndexState {
  model: string;
  semantic: boolean;
  documents: number;
  ready: number;
  running: number;
  failed: number;
  queued: number;
}

/** A passage found by vector and keyword search: a lead to read, located on its page. */
export interface LegalSearchHit {
  citation: LegalCitation;
  text: string;
  score: number;
  similarity: number | null;
  matched: ("vector" | "keyword")[];
}

export type DraftKind = "notice" | "amendment" | "letter";

/** A paralegal first draft. It has no effect until a different person approves it, and Provenant never sends it. */
export interface ContractDraft {
  id: string;
  document_id: string;
  kind: DraftKind;
  instructions: string;
  status: "queued" | "running" | "failed" | "ready" | "returned" | "approved";
  agent: string | null;
  title: string | null;
  paragraphs: { text: string; citations: LegalCitation[]; edited: boolean }[];
  open_points: string[];
  version: number;
  content_sha256: string | null;
  error: string | null;
  requested_by: string;
  created_at: string;
  history: {
    action: "generated" | "edited" | "returned" | "approved";
    version: number;
    actor: string;
    note: string | null;
    content_sha256: string;
    at: string;
  }[];
}

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
  status: "pass" | "warning" | "breach" | "not_tested" | "undetermined";
  value: string | null;
  headroom: string | null;
  workings: { label: string; amount: string; source?: string | null }[];
  note: string;
}

export interface CovenantTestDate {
  period_end: string;
  certificate_due: string;
  cure_deadline: string | null;
  cure_pending: boolean;
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
  terms: {
    first_test_date: string;
    reporting_days: number;
    cure_days: number | null;
    warning_headroom: string;
    cure_day_basis: "calendar" | "business";
    cure_runs_from: "due date" | "delivery";
    springing_usage: string | null;
    revolver_commitment: string | null;
    addback_cap: string | null;
    addback_cap_basis: "before add-backs" | "after add-backs" | null;
    frozen_gaap: boolean;
  };
  test_dates: CovenantTestDate[];
  alerts: CovenantAlert[];
  inputs: Field[];
  period_inputs: CovenantPeriodInput[];
  adjustments: CovenantAdjustment[];
}

/** An EBITDA add-back or deduction, or a lease under frozen GAAP; it counts only once a partner approves it. */
export interface CovenantAdjustment {
  id: string;
  kind: "addback" | "frozen_lease";
  quarter_end: string | null;
  label: string;
  amount: string | null;
  capped: boolean;
  lease_document_id: string | null;
  clause: string;
  basis: string;
  entered_by: string;
  approved_by: string | null;
}

/** Facts of a test date no trial balance holds; they count only once a second person confirms them. */
export interface CovenantPeriodInput {
  period_end: string;
  revolver_drawn: string | null;
  delivered_on: string | null;
  basis: string;
  entered_by: string;
  confirmed_by: string | null;
}

export function fieldLabel(name: FieldName, kind: DocumentKind = "lease"): string {
  return KINDS[kind].labels[name] ?? FIELD_LABELS[name];
}

/** The lease's terms, in display order (kept for callers that only deal with leases). */
export const FIELD_ORDER: FieldName[] = KINDS.lease.order;

const VERIFIED_STATUSES: ReadonlySet<string> = new Set(["auto_accepted", "approved", "corrected"]);

/** The lease's own terms still blocking its schedule, by the server's rule (LeaseTerms.from_fields): every
 *  required term verified; an optional one (rent steps, free months) only if present and not rejected. */
export function leaseTermsOutstanding(fields: Pick<Field, "name" | "status">[]): FieldName[] {
  const kind = KINDS.lease;
  return kind.order.filter((name) => {
    const field = fields.find((f) => f.name === name);
    if (kind.required.has(name)) return !field || !VERIFIED_STATUSES.has(field.status);
    return !!field && field.status !== "rejected" && !VERIFIED_STATUSES.has(field.status);
  });
}
