import type { FieldName } from "./types";

/** A schedule figure the user can click to see which contract terms produced it. */
export type Figure =
  | "liability"
  | "rou"
  | "opening"
  | "interest"
  | "payment"
  | "closing"
  | "depreciation"
  | "end_date";

const PRESENT_VALUE: FieldName[] = ["monthly_payment", "term_months", "annual_discount_rate", "payment_timing"];

/**
 * Which verified inputs each figure depends on, following provenant/leases/ifrs16.py:
 * the liability is the present value of the payments; interest accrues on the opening balance at the
 * monthly rate (net of the payment when paid in advance); depreciation spreads the ROU asset over the term.
 */
export const TRACE: Record<Figure, FieldName[]> = {
  liability: PRESENT_VALUE,
  rou: PRESENT_VALUE,
  opening: PRESENT_VALUE,
  closing: PRESENT_VALUE,
  depreciation: PRESENT_VALUE,
  interest: ["annual_discount_rate", "payment_timing"],
  payment: ["monthly_payment", "payment_timing"],
  end_date: ["commencement_date", "term_months"],
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
};
