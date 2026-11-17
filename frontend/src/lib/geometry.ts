import type { BBox } from "./types";

export interface Point {
  x: number;
  y: number;
}

export interface CssRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** PDF points (top-left origin, as pdfplumber reports) to CSS pixels on a page rendered at `scale`. */
export function toCss(bbox: BBox, scale: number, pad = 2): CssRect {
  const [x0, top, x1, bottom] = bbox;
  return {
    left: x0 * scale - pad,
    top: top * scale - pad,
    width: (x1 - x0) * scale + pad * 2,
    height: (bottom - top) * scale + pad * 2,
  };
}

/** A drag between two CSS-pixel points on the page, back to a PDF-point bbox (any drag direction). */
export function toPdfBox(a: Point, b: Point, scale: number): BBox {
  const round = (n: number) => Math.round((n / scale) * 100) / 100;
  return [round(Math.min(a.x, b.x)), round(Math.min(a.y, b.y)), round(Math.max(a.x, b.x)), round(Math.max(a.y, b.y))];
}

export function isUsableBox([x0, top, x1, bottom]: BBox, minPoints = 4): boolean {
  return x1 - x0 >= minPoints && bottom - top >= minPoints;
}
