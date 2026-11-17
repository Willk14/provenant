"use client";

import Link from "next/link";
import { useRef, useState } from "react";

import { DocumentStatusPill, FieldStatusPill } from "@/components/StatusPill";
import { api } from "@/lib/api";
import { formatAmount, formatDate } from "@/lib/format";
import type { Persona } from "@/lib/identity";
import { fieldLabel, KINDS, type BBox, type DocumentSummary, type Field, type FieldName } from "@/lib/types";

export interface PendingBox {
  name: FieldName;
  page: number;
  bbox: BBox;
}

interface Props {
  doc: DocumentSummary;
  persona: Persona;
  active: ReadonlySet<FieldName>;
  drawingFor: FieldName | null;
  pendingBox: PendingBox | null;
  onSelect: (name: FieldName) => void;
  onStartDrawing: (name: FieldName | null) => void;
  onCancelBox: () => void;
  onChanged: () => void;
}

/** Same shapes the server enforces (LeaseTerms.FORMATS). */
const PLACEHOLDERS: Record<FieldName, string> = {
  commencement_date: "YYYY-MM-DD",
  effective_date: "YYYY-MM-DD",
  term_months: "Months, e.g. 24",
  monthly_payment: "Amount, e.g. 120,000",
  annual_discount_rate: "Percentage, e.g. 14%",
  payment_timing: "advance or arrears",
};

export function displayValue(field: Pick<Field, "name" | "value">): string {
  switch (field.name) {
    case "monthly_payment":
      return /^[\d,]+(\.\d+)?$/.test(field.value) ? formatAmount(field.value.replace(/,/g, "")) : field.value;
    case "commencement_date":
    case "effective_date":
      return /^\d{4}-\d{2}-\d{2}$/.test(field.value) ? formatDate(field.value) : field.value;
    case "payment_timing":
      return field.value.charAt(0).toUpperCase() + field.value.slice(1);
    default:
      return field.value;
  }
}

/** Mirrors the server's checks so the UI can say why before the user tries. The server stays the authority. */
function reviewBlocker(field: Field, doc: DocumentSummary, persona: Persona): string | null {
  const roles = new Set(persona.roles);
  if (!roles.has("reviewer") && !roles.has("partner")) return "Only reviewers and partners can review.";
  if (KINDS[doc.kind].judgement.has(field.name) && !roles.has("partner")) return "A judgement call: needs a partner.";
  if (persona.user === doc.uploaded_by) return "You uploaded this document. Someone else must review it.";
  if (persona.user === field.entered_by) return "You entered this value. Someone else must review it.";
  return null;
}

function source(field: Field): string {
  if (field.extractor === "manual") return `Entered by ${field.entered_by}`;
  if (field.extractor.startsWith("claude")) return "Read by Claude";
  return "Read by label matcher";
}

export function TermsPanel(props: Props) {
  const fields = new Map(props.doc.fields.map((f) => [f.name, f]));
  return (
    <>
      {props.doc.kind === "amendment" && props.doc.parent && (
        <div className="callout">
          <strong>Amendment to {props.doc.parent.filename}</strong>
          <span className="muted">
            These are the terms it changes, from its effective date. Terms it does not mention carry over from the
            lease. Its effect appears on the{" "}
            <Link className="link" href={`/documents/${props.doc.parent.id}?tab=schedule`}>
              lease&apos;s schedule
            </Link>
            .
          </span>
        </div>
      )}
      {KINDS[props.doc.kind].order.map((name) => (
        <TermCard key={name} name={name} field={fields.get(name)} {...props} />
      ))}
      {props.doc.kind === "lease" && <AmendmentsCard lease={props.doc} onChanged={props.onChanged} />}
    </>
  );
}

function AmendmentsCard({ lease, onChanged }: { lease: DocumentSummary; onChanged: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function upload(files: FileList | null) {
    const file = files?.[0];
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      await api.upload(file, lease.id);
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <div className="term" style={{ cursor: "default" }}>
      <div className="term-head">
        <span className="term-label">Amendments, side letters and extensions</span>
        <span className="faint" style={{ fontSize: 12 }}>
          {lease.amendments.length || "None"}
        </span>
      </div>
      {lease.amendments.length > 0 && (
        <ul className="chain">
          {lease.amendments.map((a) => (
            <li key={a.id}>
              <Link href={`/documents/${a.id}`} className="link">
                {a.filename}
              </Link>
              <DocumentStatusPill status={a.status} />
            </li>
          ))}
        </ul>
      )}
      <div className="term-note">
        Each one is applied in order of its effective date. The schedule waits until every one is verified.
      </div>
      <div className="term-actions">
        <input ref={input} type="file" accept="application/pdf" hidden onChange={(e) => void upload(e.target.files)} />
        <button className="btn btn-sm" disabled={busy} onClick={() => input.current?.click()}>
          {busy ? "Uploading…" : "Upload amendment"}
        </button>
      </div>
      {error && <div className="term-error">{error}</div>}
    </div>
  );
}

function TermCard({
  name,
  field,
  doc,
  persona,
  active,
  drawingFor,
  pendingBox,
  onSelect,
  onStartDrawing,
  onCancelBox,
  onChanged,
}: Props & { name: FieldName; field: Field | undefined }) {
  const [correcting, setCorrecting] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isActive = active.has(name);
  const box = pendingBox?.name === name ? pendingBox : null;
  const canReplace = !field || field.status === "rejected";

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      setCorrecting(false);
      setDraft("");
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const kind = KINDS[doc.kind];
  const optional = !kind.required.has(name); // an amendment need not change every term
  const label = (
    <span className="term-label">
      {fieldLabel(name, doc.kind)}
      {kind.judgement.has(name) && <span className="judgement">Judgement</span>}
    </span>
  );

  const entryForm = box && (
    <form
      className="term-actions"
      onSubmit={(e) => {
        e.preventDefault();
        void run(() => api.enterField(doc.id, name, draft, box.page, box.bbox).then(onCancelBox));
      }}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="term-note" style={{ marginTop: 0, width: "100%" }}>
        Box marked on page {box.page}. Type the value exactly as the document states it.
      </div>
      <div className="inline-form">
        <input
          className="input"
          autoFocus
          value={draft}
          placeholder={PLACEHOLDERS[name]}
          onChange={(e) => setDraft(e.target.value)}
          aria-label={`${fieldLabel(name, doc.kind)} value`}
        />
        <button className="btn btn-primary btn-sm" disabled={busy || !draft.trim()}>
          Save
        </button>
        <button
          type="button"
          className="btn btn-sm"
          onClick={() => {
            setDraft("");
            setError(null);
            onCancelBox();
          }}
        >
          Cancel
        </button>
      </div>
    </form>
  );

  if (!field) {
    return (
      <div className="term" data-missing="true" data-active={drawingFor === name}>
        <div className="term-head">
          {label}
          <span className="pill">{optional ? "Unchanged" : "Not found"}</span>
        </div>
        {box ? (
          entryForm
        ) : (
          <>
            <div className="term-note">
              {optional
                ? "No change to this term was found, so the lease's value carries over. If the amendment does change it, mark where."
                : "The extractor could not find this term. Mark where the document states it."}
            </div>
            <div className="term-actions">
              <button
                className="btn btn-sm"
                aria-pressed={drawingFor === name}
                onClick={() => onStartDrawing(drawingFor === name ? null : name)}
              >
                {drawingFor === name ? "Cancel marking" : "Mark on PDF"}
              </button>
            </div>
          </>
        )}
        {error && <div className="term-error">{error}</div>}
      </div>
    );
  }

  const blocker = field.status === "needs_review" ? reviewBlocker(field, doc, persona) : null;

  return (
    <div
      className="term"
      data-active={isActive}
      role="button"
      tabIndex={0}
      onClick={() => onSelect(name)}
      onKeyDown={(e) => e.key === "Enter" && e.target === e.currentTarget && onSelect(name)}
    >
      <div className="term-head">
        {label}
        <FieldStatusPill status={field.status} />
      </div>
      <div className="term-value">{displayValue(field)}</div>
      <div className="term-meta">
        <span>Page {field.page}</span>
        <span>{source(field)}</span>
        {field.extractor !== "manual" && <span className="num">{Math.round(field.confidence * 100)}% confidence</span>}
      </div>

      {field.status === "needs_review" && (
        <div className="term-actions" onClick={(e) => e.stopPropagation()}>
          {correcting ? (
            <form
              className="inline-form"
              onSubmit={(e) => {
                e.preventDefault();
                void run(() => api.review(field.id, "corrected", draft));
              }}
            >
              <input
                className="input"
                autoFocus
                value={draft}
                placeholder={PLACEHOLDERS[name]}
                onChange={(e) => setDraft(e.target.value)}
                aria-label="Corrected value"
              />
              <button className="btn btn-primary btn-sm" disabled={busy || !draft.trim()}>
                Save
              </button>
              <button type="button" className="btn btn-sm" onClick={() => setCorrecting(false)}>
                Cancel
              </button>
            </form>
          ) : (
            <>
              <button
                className="btn btn-primary btn-sm"
                disabled={busy || !!blocker}
                onClick={() => void run(() => api.review(field.id, "approved"))}
              >
                Approve
              </button>
              <button
                className="btn btn-sm"
                disabled={busy || !!blocker}
                onClick={() => {
                  setDraft(field.value);
                  setCorrecting(true);
                }}
              >
                Correct
              </button>
              <button
                className="btn btn-sm btn-ghost btn-danger"
                disabled={busy || !!blocker}
                onClick={() => void run(() => api.review(field.id, "rejected"))}
              >
                Reject
              </button>
            </>
          )}
          {blocker && <div className="term-note" style={{ width: "100%" }}>{blocker}</div>}
        </div>
      )}

      {canReplace && (
        <div onClick={(e) => e.stopPropagation()}>
          {box ? (
            entryForm
          ) : (
            <div className="term-actions">
              <button className="btn btn-sm" onClick={() => onStartDrawing(drawingFor === name ? null : name)}>
                {drawingFor === name ? "Cancel marking" : "Mark the correct value on PDF"}
              </button>
            </div>
          )}
        </div>
      )}
      {error && <div className="term-error">{error}</div>}
    </div>
  );
}
