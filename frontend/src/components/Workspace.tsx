"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AskTab } from "@/components/AskTab";
import { CovenantsPanel } from "@/components/CovenantsPanel";
import { DraftingPanel } from "@/components/DraftingPanel";
import { LegalReviewPanel } from "@/components/LegalReviewPanel";
import { PdfViewer, type Highlight, type HighlightName } from "@/components/PdfViewer";
import { SchedulePanel } from "@/components/SchedulePanel";
import { DocumentStatusPill } from "@/components/StatusPill";
import { TermsPanel, type PendingBox } from "@/components/TermsPanel";
import { api, ApiError } from "@/lib/api";
import { formatDate, shortHash } from "@/lib/format";
import { resolveInputs, type Selection } from "@/lib/trace";
import {
  fieldLabel,
  KINDS,
  leaseTermsOutstanding,
  type DocumentSummary,
  type FieldName,
  type LegalCitation,
  type Schedule,
} from "@/lib/types";
import { usePersona } from "@/lib/usePersona";

export type Tab = "terms" | "schedule" | "review" | "drafts" | "ask";

export function Workspace({ id, initialField, initialTab, initialCitation }: {
  id: string;
  initialField?: FieldName;
  initialTab?: Tab;
  initialCitation?: { page: number; bbox: LegalCitation["bbox"] };
}) {
  const persona = usePersona();
  const [doc, setDoc] = useState<DocumentSummary | null>(null);
  const [pdf, setPdf] = useState<ArrayBuffer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>(initialTab ?? "terms");
  const [schedule, setSchedule] = useState<Schedule | null>(null);
  const [blocked, setBlocked] = useState<string | null>(null);
  const [selectedField, setSelectedField] = useState<FieldName | null>(initialField ?? null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [focus, setFocus] = useState<{ name: HighlightName; nonce: number } | null>(
    initialCitation ? { name: "finding:link", nonce: 1 } : initialField ? { name: initialField, nonce: 1 } : null,
  );
  const [cited, setCited] = useState<{ id: string; citation: LegalCitation } | null>(
    initialCitation
      ? { id: "link", citation: { document_id: id, filename: "", quote: "", ...initialCitation } }
      : null,
  );
  const [drawingFor, setDrawingFor] = useState<FieldName | null>(null);
  const [pendingBox, setPendingBox] = useState<PendingBox | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await api.document(id);
      setDoc(next);
      setError(null);
      if (next.kind !== "lease" || next.status !== "extracted") return;
      // Don't ask for a schedule the server will refuse over the lease's own terms; say what is outstanding.
      // Amendments are checked by the server, which names the one that blocks.
      const outstanding = leaseTermsOutstanding(next.fields);
      if (outstanding.length) {
        setSchedule(null);
        setBlocked(`Still to verify: ${outstanding.map((n) => fieldLabel(n).toLowerCase()).join(", ")}`);
        return;
      }
      try {
        setSchedule(await api.schedule(id));
        setBlocked(null);
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 409)) throw e;
        setSchedule(null);
        setBlocked(e.message.replace(/_/g, " "));
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

  const reading = (d: { status: string }) => d.status === "queued" || d.status === "extracting";
  const extracting = !!doc && reading(doc);
  const busy = extracting || !!doc?.amendments.some(reading); // a new amendment is being read
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(load, 1200);
    return () => clearInterval(timer);
  }, [busy, load]);

  const kind = doc?.kind ?? "lease";
  const citing = tab === "review" || tab === "drafts"; // these tabs point at quoted wording on the PDF
  const highlights: Highlight[] = useMemo(() => {
    const terms: Highlight[] = (doc?.fields ?? []).filter((f) => f.page >= 1).map((f) => ({
      id: f.id,
      name: f.name,
      label: fieldLabel(f.name, kind),
      page: f.page,
      bbox: f.bbox,
      status: f.status,
      manual: f.extractor === "manual",
    }));
    if (!citing || !cited) return terms;
    return [...terms, {
      id: `finding-${cited.id}`,
      name: `finding:${cited.id}` as const,
      label: "Cited wording",
      page: cited.citation.page,
      bbox: cited.citation.bbox,
      status: "needs_review",
      manual: false,
    }];
  }, [doc, kind, citing, cited]);

  // On the schedule tab, light up exactly the fields behind the selected figure that live in this PDF.
  const tracedHere = useMemo(
    () => (schedule && selection ? resolveInputs(schedule, selection).filter((f) => f.document_id === id) : []),
    [schedule, selection, id],
  );
  const [covenantClause, setCovenantClause] = useState<FieldName | null>(null);
  const active = useMemo<ReadonlySet<FieldName>>(() => {
    if (citing) return new Set();
    if (tab === "schedule" && kind === "credit_agreement") return new Set(covenantClause ? [covenantClause] : []);
    if (tab === "schedule" && selection) return new Set(tracedHere.map((f) => f.name));
    return new Set(selectedField ? [selectedField] : []);
  }, [tab, citing, kind, covenantClause, selection, tracedHere, selectedField]);

  const filenames = useMemo(() => {
    const names = new Map<string, string>();
    if (doc) names.set(doc.id, doc.filename);
    doc?.amendments.forEach((a) => names.set(a.id, a.filename));
    schedule?.remeasurements.forEach((r) => names.set(r.document_id, r.filename));
    return names;
  }, [doc, schedule]);

  const focusField = (name: FieldName) => {
    setSelectedField(name);
    setFocus({ name, nonce: Date.now() });
  };

  const selectFigure = (s: Selection) => {
    setSelection(s);
    const here = schedule ? resolveInputs(schedule, s).find((f) => f.document_id === id) : undefined;
    if (here) setFocus({ name: here.name, nonce: Date.now() });
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
  const missing = doc
    ? [...KINDS[kind].required].filter((n) => !doc.fields.some((f) => f.name === n && f.status !== "rejected")).length
    : 0;

  return (
    <div className="workspace">
      <section className="ws-left">
        <div className="ws-head">
          <Link
            href={doc?.parent ? `/documents/${doc.parent.id}` : "/documents"}
            className="btn btn-sm btn-ghost"
            aria-label={doc?.parent ? "Back to the lease" : "Back to documents"}
          >
            ←
          </Link>
          <div className="ws-title">
            <h1>
              {doc?.kind === "amendment" && <span className="kind-tag">Amendment</span>}
              {doc?.filename ?? "Loading…"}
            </h1>
            {doc && (
              <div className="meta">
                {doc.parent && (
                  <>
                    Amends{" "}
                    <Link className="link" href={`/documents/${doc.parent.id}`}>
                      {doc.parent.filename}
                    </Link>{" "}
                    ·{" "}
                  </>
                )}
                Uploaded by {doc.uploaded_by} on {formatDate(doc.created_at)} ·{" "}
                <span className="mono" title={`SHA-256 ${doc.sha256}`}>
                  sha256 {shortHash(doc.sha256)}
                </span>
              </div>
            )}
          </div>
          <div className="ws-tools">
            {doc && <DocumentStatusPill status={doc.status} needsReview={toReview} missing={missing} />}
          </div>
        </div>
        {drawingFor && (
          <div className="draw-hint">
            Drag a box around where the document states the {fieldLabel(drawingFor, kind).toLowerCase()}.
            <button className="btn btn-sm" onClick={() => setDrawingFor(null)}>
              Cancel
            </button>
          </div>
        )}
        {error && (
          <div className="error-banner" style={{ margin: 12 }}>
            {error}
          </div>
        )}
        {pdf ? (
          <PdfViewer
            data={pdf}
            highlights={highlights}
            active={citing && cited ? new Set<HighlightName>([`finding:${cited.id}`]) : active}
            focus={focus}
            drawing={drawingFor !== null}
            onSelect={(name) => {
              if (name.startsWith("finding:")) return;
              setTab("terms");
              focusField(name as FieldName);
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
            {kind === "amendment" ? "Changed terms" : "Contract terms"}{" "}
            {toReview > 0 && <span className="tab-count">{toReview}</span>}
          </button>
          <button role="tab" aria-selected={tab === "schedule"} onClick={() => setTab("schedule")}>
            {kind === "credit_agreement" ? "Covenants" : "IFRS 16 schedule"}
          </button>
          {kind !== "amendment" && (
            <button role="tab" aria-selected={tab === "review"} onClick={() => setTab("review")}>
              Legal review
            </button>
          )}
          {kind !== "amendment" && (
            <button role="tab" aria-selected={tab === "drafts"} onClick={() => setTab("drafts")}>
              Drafting
            </button>
          )}
          {kind !== "amendment" && (
            <button role="tab" aria-selected={tab === "ask"} onClick={() => setTab("ask")}>
              Ask Claude
            </button>
          )}
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
            <div className="error-banner">Extraction failed: {doc.error}. Upload the file again to retry.</div>
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
          ) : tab === "review" && doc.kind !== "amendment" ? (
            <LegalReviewPanel
              doc={doc}
              onCite={(citedId, citation) => {
                setCited({ id: citedId, citation });
                setFocus({ name: `finding:${citedId}`, nonce: Date.now() });
              }}
            />
          ) : tab === "drafts" && doc.kind !== "amendment" ? (
            <DraftingPanel
              doc={doc}
              onCite={(citedId, citation) => {
                setCited({ id: citedId, citation });
                setFocus({ name: `finding:${citedId}`, nonce: Date.now() });
              }}
            />
          ) : tab === "ask" && doc.kind !== "amendment" ? (
            <AskTab contractId={doc.id} filename={doc.filename} />
          ) : doc.kind === "credit_agreement" ? (
            <CovenantsPanel
              doc={doc}
              onTrace={(name) => {
                setCovenantClause(name);
                if (name) setFocus({ name, nonce: Date.now() });
              }}
            />
          ) : doc.kind === "amendment" && doc.parent ? (
            <div className="callout">
              <strong>One schedule per lease</strong>
              <span className="muted">
                This amendment remeasures the schedule of {doc.parent.filename} from its effective date.{" "}
              </span>
              <Link className="btn btn-sm" style={{ marginTop: 8 }} href={`/documents/${doc.parent.id}?tab=schedule`}>
                Open the lease&apos;s schedule
              </Link>
            </div>
          ) : (
            <SchedulePanel
              documentId={id}
              schedule={schedule}
              blocked={blocked}
              selection={selection}
              filenames={filenames}
              onSelectFigure={selectFigure}
              onFocusField={focusField}
              onDownload={download}
            />
          )}
        </div>
      </aside>
    </div>
  );
}
