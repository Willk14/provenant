"use client";

import { useState } from "react";

import { FieldStatusPill } from "@/components/StatusPill";
import { api } from "@/lib/api";
import { formatAmount, formatDate } from "@/lib/format";
import type { Persona } from "@/lib/identity";
import { FIELD_LABELS, FIELD_ORDER, JUDGEMENT_FIELDS, type BBox, type DocumentSummary, type Field, type FieldName } from "@/lib/types";

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
  if (JUDGEMENT_FIELDS.has(field.name) && !roles.has("partner")) return "A judgement call: needs a partner.";
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
      {FIELD_ORDER.map((name) => (
        <TermCard key={name} name={name} field={fields.get(name)} {...props} />
      ))}
    </>
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

  const label = (
    <span className="term-label">
      {FIELD_LABELS[name]}
      {JUDGEMENT_FIELDS.has(name) && <span className="judgement">Judgement</span>}
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
          aria-label={`${FIELD_LABELS[name]} value`}
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
          <span className="pill">Not found</span>
        </div>
        {box ? (
          entryForm
        ) : (
          <>
            <div className="term-note">The extractor could not find this term. Mark where the document states it.</div>
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
