"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { PdfViewer, type Highlight } from "@/components/PdfViewer";
import { SchedulePanel, type Selection } from "@/components/SchedulePanel";
import { DocumentStatusPill } from "@/components/StatusPill";
import { TermsPanel, type PendingBox } from "@/components/TermsPanel";
import { api, ApiError } from "@/lib/api";
import { formatDate, shortHash } from "@/lib/format";
import { TRACE } from "@/lib/trace";
import { FIELD_LABELS, FIELD_ORDER, type DocumentSummary, type FieldName, type Schedule } from "@/lib/types";
import { usePersona } from "@/lib/usePersona";

type Tab = "terms" | "schedule";

export function Workspace({ id, initialField }: { id: string; initialField?: FieldName }) {
  const persona = usePersona();
  const [doc, setDoc] = useState<DocumentSummary | null>(null);
  const [pdf, setPdf] = useState<ArrayBuffer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("terms");
  const [schedule, setSchedule] = useState<Schedule | null>(null);
  const [blocked, setBlocked] = useState<string | null>(null);
  const [selectedField, setSelectedField] = useState<FieldName | null>(initialField ?? null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [focus, setFocus] = useState<{ name: FieldName; nonce: number } | null>(
    initialField ? { name: initialField, nonce: 1 } : null,
  );
  const [drawingFor, setDrawingFor] = useState<FieldName | null>(null);
  const [pendingBox, setPendingBox] = useState<PendingBox | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await api.document(id);
      setDoc(next);
      setError(null);
      // Don't ask for a schedule the server will refuse; say what is outstanding instead (the server still decides).
      const verified = new Set(["auto_accepted", "approved", "corrected"]);
      const outstanding = FIELD_ORDER.filter((n) => !next.fields.some((f) => f.name === n && verified.has(f.status)));
      if (next.status === "extracted" && outstanding.length) {
        setSchedule(null);
        setBlocked(`Still to verify: ${outstanding.map((n) => FIELD_LABELS[n].toLowerCase()).join(", ")}`);
      } else if (next.status === "extracted") {
        try {
          setSchedule(await api.schedule(id));
          setBlocked(null);
        } catch (e) {
          if (e instanceof ApiError && e.status === 409) {
            setSchedule(null);
            setBlocked(e.message.replace(/_/g, " "));
          } else throw e;
        }
      }
    } catch (e) {
      setError((e as Error).message);
    }
  }, [id]);

  useEffect(() => {
    if (!persona) return;
    void load();
    api
      .pdf(id)
      .then(setPdf)
      .catch((e: Error) => setError(e.message));
  }, [persona, id, load]);

  const extracting = doc?.status === "queued" || doc?.status === "extracting";
  useEffect(() => {
    if (!extracting) return;
    const timer = setInterval(load, 1200);
    return () => clearInterval(timer);
  }, [extracting, load]);

  const highlights: Highlight[] = useMemo(
    () =>
      (doc?.fields ?? []).map((f) => ({
        id: f.id,
        name: f.name,
        label: FIELD_LABELS[f.name],
        page: f.page,
        bbox: f.bbox,
        status: f.status,
        manual: f.extractor === "manual",
      })),
    [doc],
  );

  const active = useMemo<ReadonlySet<FieldName>>(() => {
    if (tab === "schedule" && selection) return new Set(TRACE[selection.figure]);
    return new Set(selectedField ? [selectedField] : []);
  }, [tab, selection, selectedField]);

  const focusField = (name: FieldName) => {
    setSelectedField(name);
    setFocus({ name, nonce: Date.now() });
  };

  const selectFigure = (s: Selection) => {
    setSelection(s);
    const first = TRACE[s.figure].find((n) => doc?.fields.some((f) => f.name === n));
    if (first) setFocus({ name: first, nonce: Date.now() });
  };

  async function download() {
    try {
      const bytes = await api.workbook(id);
      const url = URL.createObjectURL(
        new Blob([bytes], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }),
      );
      const a = Object.assign(document.createElement("a"), { href: url, download: `lease-schedule-${id}.xlsx` });
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const toReview = doc?.fields.filter((f) => f.status === "needs_review").length ?? 0;
  const missing = doc ? FIELD_ORDER.filter((n) => !doc.fields.some((f) => f.name === n && f.status !== "rejected")).length : 0;

  return (
    <div className="workspace">
      <section className="ws-left">
        <div className="ws-head">
          <Link href="/" className="btn btn-sm btn-ghost" aria-label="Back to documents">
            ←
          </Link>
          <div className="ws-title">
            <h1>{doc?.filename ?? "Loading…"}</h1>
            {doc && (
              <div className="meta">
                Uploaded by {doc.uploaded_by} on {formatDate(doc.created_at)} ·{" "}
                <span className="mono" title={`SHA-256 ${doc.sha256}`}>
                  sha256 {shortHash(doc.sha256)}
                </span>
              </div>
            )}
          </div>
          <div className="ws-tools">{doc && <DocumentStatusPill status={doc.status} needsReview={toReview} missing={missing} />}</div>
        </div>
        {drawingFor && (
          <div className="draw-hint">
            Drag a box around where the document states the {FIELD_LABELS[drawingFor].toLowerCase()}.
            <button className="btn btn-sm" onClick={() => setDrawingFor(null)}>
              Cancel
            </button>
          </div>
        )}
        {error && <div className="error-banner" style={{ margin: 12 }}>{error}</div>}
        {pdf ? (
          <PdfViewer
            data={pdf}
            highlights={highlights}
            active={active}
            focus={focus}
            drawing={drawingFor !== null}
            onSelect={(name) => {
              setTab("terms");
              focusField(name);
            }}
            onDraw={(page, bbox) => {
              if (!drawingFor) return;
              setPendingBox({ name: drawingFor, page, bbox });
              setDrawingFor(null);
              setTab("terms");
            }}
          />
        ) : (
          <div className="pdf-loading">Opening document…</div>
        )}
      </section>

      <aside className="panel">
        <div className="tabs" role="tablist">
          <button role="tab" aria-selected={tab === "terms"} onClick={() => setTab("terms")}>
            Contract terms {toReview > 0 && <span className="tab-count">{toReview}</span>}
          </button>
          <button role="tab" aria-selected={tab === "schedule"} onClick={() => setTab("schedule")}>
            IFRS 16 schedule
          </button>
        </div>
        <div className="panel-body">
          {!doc || !persona ? (
            <div className="empty">Loading…</div>
          ) : extracting ? (
            <div className="callout">
              <strong>Reading the document…</strong>
              <span className="muted">Terms appear here as soon as extraction finishes.</span>
            </div>
          ) : doc.status === "failed" ? (
            <div className="error-banner">
              Extraction failed: {doc.error}. Upload the file again to retry.
            </div>
          ) : tab === "terms" ? (
            <TermsPanel
              doc={doc}
              persona={persona}
              active={active}
              drawingFor={drawingFor}
              pendingBox={pendingBox}
              onSelect={focusField}
              onStartDrawing={(name) => {
                setPendingBox(null);
                setDrawingFor(name);
              }}
              onCancelBox={() => setPendingBox(null)}
              onChanged={load}
            />
          ) : (
            <SchedulePanel
              schedule={schedule}
              blocked={blocked}
              selection={selection}
              onSelectFigure={selectFigure}
              onFocusField={(name) => setFocus({ name, nonce: Date.now() })}
              onDownload={download}
            />
          )}
        </div>
      </aside>
    </div>
  );
}
