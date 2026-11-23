import { describe, expect, it } from "vitest";

import { formatAmount, formatDate } from "./format";
import { isUsableBox, toCss, toPdfBox } from "./geometry";
import { resolveInputs, TRACE } from "./trace";
import { KINDS, leaseTermsOutstanding, type Field, type FieldName, type Schedule } from "./types";

describe("formatAmount", () => {
  it("groups thousands without float rounding", () => {
    expect(formatAmount("11255.08")).toBe("11,255.08");
    expect(formatAmount("1234567890123.45")).toBe("1,234,567,890,123.45");
    expect(formatAmount("0.1")).toBe("0.10");
    expect(formatAmount("1000")).toBe("1,000.00");
    expect(formatAmount("-0.03")).toBe("−0.03");
  });
});

describe("formatDate", () => {
  it("formats ISO dates without timezone drift", () => {
    expect(formatDate("2026-01-31")).toBe("31 Jan 2026");
    expect(formatDate("2026-01-31", "Pacific/Honolulu")).toBe("31 Jan 2026"); // a calendar date never moves
  });

  it("shows a timestamp on the reader's own date", () => {
    // 21:46 UTC on 23 Nov is 00:46 on 24 Nov in Nairobi (UTC+3).
    expect(formatDate("2026-11-23T21:46:00.123456Z", "Africa/Nairobi")).toBe("24 Nov 2026");
    expect(formatDate("2026-11-23T21:46:00.123456+00:00", "Africa/Nairobi")).toBe("24 Nov 2026");
    expect(formatDate("2026-11-23T21:46:00", "Africa/Nairobi")).toBe("24 Nov 2026"); // no offset: stored as UTC
    expect(formatDate("2026-11-23T20:59:59Z", "Africa/Nairobi")).toBe("23 Nov 2026");
  });
});

describe("geometry", () => {
  it("round-trips a drag through CSS pixels and back", () => {
    const box = toPdfBox({ x: 300, y: 160 }, { x: 100, y: 120 }, 2);
    expect(box).toEqual([50, 60, 150, 80]);
    const css = toCss(box, 2, 0);
    expect(css).toEqual({ left: 100, top: 120, width: 200, height: 40 });
  });

  it("rejects clicks that are not boxes", () => {
    expect(isUsableBox([10, 10, 11, 30])).toBe(false);
    expect(isUsableBox([10, 10, 30, 30])).toBe(true);
  });
});

describe("TRACE", () => {
  it("only references real terms, and every lease or amendment term feeds some figure", () => {
    const terms = new Set([...KINDS.lease.order, ...KINDS.amendment.order]);
    const used = new Set(Object.values(TRACE).flat());
    for (const name of used) expect(terms.has(name)).toBe(true);
    for (const name of terms) expect(used.has(name)).toBe(true);
  });
});

describe("resolveInputs", () => {
  const field = (id: string, name: FieldName, document_id: string): Field => ({
    id, name, document_id, value: "", confidence: 1, status: "approved", gate_version: null, extractor: "x",
    entered_by: null, page: 1, bbox: [0, 0, 1, 1], char_start: 0, char_end: 1,
  });
  const schedule = {
    inputs: [
      field("L-pay", "monthly_payment", "lease"),
      field("L-timing", "payment_timing", "lease"),
      field("A-pay", "monthly_payment", "amend"),
    ],
    segments: [
      { document_id: "lease", from_period: 1, inputs: { monthly_payment: "L-pay", payment_timing: "L-timing" } },
      { document_id: "amend", from_period: 7, inputs: { monthly_payment: "A-pay", payment_timing: "L-timing" } },
    ],
  } as unknown as Schedule;

  it("takes a period's terms from the document that governed it", () => {
    const ids = resolveInputs(schedule, { figure: "payment", period: 8, segment: 1 }).map((f) => f.id);
    expect(ids).toEqual(["A-pay", "L-timing"]);
  });

  it("collects every segment for lease-wide totals, without duplicates", () => {
    const ids = resolveInputs(schedule, { figure: "payment", period: null, segment: null }).map((f) => f.id);
    expect(ids).toEqual(["L-pay", "A-pay", "L-timing"]);
  });
});

describe("leaseTermsOutstanding", () => {
  const ok = (name: FieldName) => ({ name, status: "approved" as const });
  const required = ["commencement_date", "term_months", "monthly_payment", "payment_timing", "annual_discount_rate"] as const;

  it("does not wait for optional rent steps or free months the lease does not have", () => {
    expect(leaseTermsOutstanding(required.map(ok))).toEqual([]);
  });

  it("waits for missing required terms and for optional ones found but not yet verified", () => {
    const fields = [...required.slice(1).map(ok), { name: "rent_steps" as const, status: "needs_review" as const }];
    expect(leaseTermsOutstanding(fields)).toEqual(["commencement_date", "rent_steps"]);
  });

  it("treats a rejected optional term as absent, like the server", () => {
    const fields = [...required.map(ok), { name: "free_periods" as const, status: "rejected" as const }];
    expect(leaseTermsOutstanding(fields)).toEqual([]);
  });
});
