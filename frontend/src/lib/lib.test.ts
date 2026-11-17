import { describe, expect, it } from "vitest";

import { formatAmount, formatDate } from "./format";
import { isUsableBox, toCss, toPdfBox } from "./geometry";
import { TRACE } from "./trace";
import { FIELD_ORDER } from "./types";

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
  it("only references real lease terms, and every term feeds some figure", () => {
    const used = new Set(Object.values(TRACE).flat());
    for (const name of used) expect(FIELD_ORDER).toContain(name);
    for (const name of FIELD_ORDER) expect(used.has(name)).toBe(true);
  });
});
