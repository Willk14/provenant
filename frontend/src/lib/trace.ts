import type { Field, FieldName, Schedule } from "./types";

/** A schedule figure the user can click to see which contract terms produced it. */
export type Figure =
  | "liability"
  | "rou"
  | "opening"
  | "interest"
  | "payment"
  | "closing"
  | "depreciation"
  | "end_date"
  | "remeasurement";

const PRESENT_VALUE: FieldName[] = [
  "monthly_payment",
  "rent_steps",
  "free_periods",
  "term_months",
  "annual_discount_rate",
  "payment_timing",
];

/**
 * Which verified inputs each figure depends on, following provenant/leases/ifrs16.py:
 * the liability is the present value of the payments; interest accrues on the opening balance at the
 * monthly rate (net of the payment when paid in advance); depreciation spreads the ROU asset over the term;
 * a remeasurement re-prices the remaining payments from the effective date at the revised rate.
 */
export const TRACE: Record<Figure, FieldName[]> = {
  liability: PRESENT_VALUE,
  rou: PRESENT_VALUE,
  opening: PRESENT_VALUE,
  closing: PRESENT_VALUE,
  depreciation: PRESENT_VALUE,
  interest: ["annual_discount_rate", "payment_timing"],
  payment: ["monthly_payment", "rent_steps", "free_periods", "payment_timing"],
  end_date: ["commencement_date", "term_months"],
  remeasurement: [
    "effective_date",
    "terms_start_date",
    "expansion_treatment",
    "monthly_payment",
    "rent_steps",
    "free_periods",
    "expiry_date",
    "term_months",
    "annual_discount_rate",
  ],
};

export const FIGURE_LABELS: Record<Figure, string> = {
  liability: "Lease liability at commencement",
  rou: "Right-of-use asset",
  opening: "Opening liability",
  interest: "Interest",
  payment: "Payment",
  closing: "Closing liability",
  depreciation: "Depreciation",
  end_date: "Period end",
  remeasurement: "Remeasurement",
};

export interface Selection {
  figure: Figure;
  period: number | null; // null = a summary figure
  segment: number | null; // which terms governed it; null = every segment (lease-wide totals)
}

/**
 * The exact fields behind a figure. After an amendment, a period's payment comes from the amendment's
 * field, while unchanged terms (say, the commencement date) still come from the lease.
 */
export function resolveInputs(schedule: Schedule, selection: Selection): Field[] {
  const byId = new Map(schedule.inputs.map((f) => [f.id, f]));
  const segments = selection.segment === null ? schedule.segments : [schedule.segments[selection.segment]];
  const seen = new Set<string>();
  const out: Field[] = [];
  for (const name of TRACE[selection.figure]) {
    for (const segment of segments) {
      const id = segment?.inputs[name];
      const field = id ? byId.get(id) : undefined;
      if (field && !seen.has(field.id)) {
        seen.add(field.id);
        out.push(field);
      }
    }
  }
  return out;
}
