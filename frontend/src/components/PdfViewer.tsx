"use client";

import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { isUsableBox, toCss, toPdfBox, type Point } from "@/lib/geometry";
import type { BBox, FieldName, FieldStatus } from "@/lib/types";

/** A contract term, or a cited passage from a legal review finding. */
export type HighlightName = FieldName | `finding:${string}`;

export interface Highlight {
  id: string;
  name: HighlightName;
  label: string;
  page: number;
  bbox: BBox;
  status: FieldStatus;
  manual: boolean;
}

interface Props {
  data: ArrayBuffer;
  highlights: Highlight[];
  active: ReadonlySet<HighlightName>;
  focus: { name: HighlightName; nonce: number } | null;
  drawing: boolean;
  onDraw: (page: number, bbox: BBox) => void;
  onSelect: (name: HighlightName) => void;
}

interface Size {
  w: number;
  h: number;
}

const ZOOMS = [0.75, 1, 1.25, 1.5, 2];

export function PdfViewer({ data, highlights, active, focus, drawing, onDraw, onSelect }: Props) {
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [sizes, setSizes] = useState<Size[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [fitWidth, setFitWidth] = useState(0);
  const scroller = useRef<HTMLDivElement>(null);
  const pages = useRef<(HTMLDivElement | null)[]>([]);

  useEffect(() => {
    let cancelled = false;
    let loaded: PDFDocumentProxy | null = null;
    (async () => {
      const pdfjs = await import("pdfjs-dist");
      pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";
      // pdf.js transfers the buffer to its worker, so hand it a copy.
      loaded = await pdfjs.getDocument({ data: new Uint8Array(data.slice(0)) }).promise;
      const measured: Size[] = [];
      for (let i = 1; i <= loaded.numPages; i++) {
        const viewport = (await loaded.getPage(i)).getViewport({ scale: 1 });
        measured.push({ w: viewport.width, h: viewport.height });
      }
      if (!cancelled) {
        setDoc(loaded);
        setSizes(measured);
      }
    })().catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
      void loaded?.destroy();
    };
  }, [data]);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const observer = new ResizeObserver(() => setFitWidth(el.clientWidth - 40));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const widest = Math.max(0, ...sizes.map((s) => s.w));
  const scale = fitWidth > 0 && widest > 0 ? Math.min(fitWidth / widest, 1.6) * zoom : 0;

  // Scroll the focused field's box into the upper third of the view.
  useEffect(() => {
    if (!focus || !scale) return;
    const target = highlights.find((h) => h.name === focus.name);
    const pageEl = target && pages.current[target.page - 1];
    const el = scroller.current;
    if (!target || !pageEl || !el) return;
    el.scrollTo({ top: pageEl.offsetTop + target.bbox[1] * scale - el.clientHeight / 3, behavior: "smooth" });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only on an explicit focus request
  }, [focus?.nonce, scale]);

  return (
    <div className="pdf-wrap">
      <div className="pdf-scroll" ref={scroller}>
        {error && <div className="error-banner">Could not open this PDF: {error}</div>}
        {!doc && !error && <div className="pdf-loading">Opening document…</div>}
        {doc &&
          scale > 0 &&
          sizes.map((size, i) => (
            <PdfPage
              key={i}
              doc={doc}
              number={i + 1}
              size={size}
              scale={scale}
              ref={(el) => {
                pages.current[i] = el;
              }}
              drawing={drawing}
              onDraw={onDraw}
            >
              {highlights
                .filter((h) => h.page === i + 1)
                .map((h) => {
                  const isActive = active.has(h.name);
                  const css = toCss(h.bbox, scale);
                  return (
                    <button
                      key={h.id}
                      type="button"
                      className="hl"
                      data-status={h.status}
                      data-manual={h.manual}
                      data-active={isActive}
                      data-dim={active.size > 0 && !isActive}
                      style={{ ...css, pointerEvents: drawing ? "none" : undefined }}
                      title={`${h.label}: page ${h.page}`}
                      aria-label={`${h.label} on page ${h.page}`}
                      onClick={() => onSelect(h.name)}
                    >
                      {isActive && <span className="hl-tag">{h.label}</span>}
                    </button>
                  );
                })}
            </PdfPage>
          ))}
      </div>
      {doc && (
        <div className="zoom" role="group" aria-label="Zoom">
          <button
            className="btn btn-sm btn-ghost"
            aria-label="Zoom out"
            disabled={zoom === ZOOMS[0]}
            onClick={() => setZoom(ZOOMS[Math.max(0, ZOOMS.indexOf(zoom) - 1)])}
          >
            −
          </button>
          <span className="num">{Math.round(zoom * 100)}%</span>
          <button
            className="btn btn-sm btn-ghost"
            aria-label="Zoom in"
            disabled={zoom === ZOOMS[ZOOMS.length - 1]}
            onClick={() => setZoom(ZOOMS[Math.min(ZOOMS.length - 1, ZOOMS.indexOf(zoom) + 1)])}
          >
            +
          </button>
        </div>
      )}
    </div>
  );
}

interface PageProps {
  doc: PDFDocumentProxy;
  number: number;
  size: Size;
  scale: number;
  drawing: boolean;
  onDraw: (page: number, bbox: BBox) => void;
  children: ReactNode;
  ref: (el: HTMLDivElement | null) => void;
}

function PdfPage({ doc, number, size, scale, drawing, onDraw, children, ref }: PageProps) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [start, setStart] = useState<Point | null>(null);
  const [end, setEnd] = useState<Point | null>(null);

  useEffect(() => {
    let task: RenderTask | null = null;
    let cancelled = false;
    (async () => {
      const page = await doc.getPage(number);
      const el = canvas.current;
      if (cancelled || !el) return;
      const viewport = page.getViewport({ scale });
      const ratio = window.devicePixelRatio || 1;
      el.width = Math.floor(viewport.width * ratio);
      el.height = Math.floor(viewport.height * ratio);
      task = page.render({
        canvasContext: el.getContext("2d")!,
        viewport,
        transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
      });
      await task.promise;
    })().catch((e: Error) => {
      if (e?.name !== "RenderingCancelledException") console.error(e);
    });
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [doc, number, scale]);

  const local = (e: React.PointerEvent<HTMLDivElement>): Point => {
    const rect = e.currentTarget.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  };

  const draft = start && end ? toCss(toPdfBox(start, end, scale), scale, 0) : null;

  return (
    <div className="pdf-page" ref={ref} style={{ width: size.w * scale, height: size.h * scale }}>
      <span className="pdf-page-label">Page {number}</span>
      <canvas ref={canvas} style={{ width: size.w * scale, height: size.h * scale }} aria-label={`Page ${number}`} />
      <div
        className="overlay"
        data-drawing={drawing}
        onPointerDown={(e) => {
          if (!drawing) return;
          e.currentTarget.setPointerCapture(e.pointerId);
          setStart(local(e));
          setEnd(local(e));
        }}
        onPointerMove={(e) => start && setEnd(local(e))}
        onPointerUp={(e) => {
          if (start) {
            const box = toPdfBox(start, local(e), scale);
            if (isUsableBox(box)) onDraw(number, box);
          }
          setStart(null);
          setEnd(null);
        }}
      >
        {children}
        {draft && <div className="draft-box" style={draft} />}
      </div>
    </div>
  );
}
