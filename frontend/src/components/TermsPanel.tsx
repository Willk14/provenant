"use client";

import Link from "next/link";
import { useRef, useState } from "react";

import { DocumentStatusPill, FieldStatusPill } from "@/components/StatusPill";
import { api } from "@/lib/api";
import { formatAmount, formatDate } from "@/lib/format";
import type { Persona } from "@/lib/identity";
import { fieldLabel, INPUT_FIELDS, INPUT_ONLY_FIELDS, KINDS, type BBox, type DocumentSummary, type Field, type FieldName } from "@/lib/types";

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
  terms_start_date: "YYYY-MM-DD",
  expiry_date: "YYYY-MM-DD",
  expansion_treatment: "separate lease or modification",
  term_months: "Months, e.g. 24",
  monthly_payment: "Amount, e.g. 120,000",
  annual_discount_rate: "Percentage, e.g. 14%",
  payment_timing: "advance or arrears",
  rent_steps: "e.g. 13: 1,030.00; 25: 1,060.90  or  2026-06-01: 1,030.00",
  free_periods: "e.g. 1, 2  or  2025-04, 2025-05",
  max_leverage_ratio: "Ratio, e.g. 3.50x",
  min_interest_cover: "Ratio, e.g. 4.00x",
  min_liquidity: "Amount, e.g. 50,000,000",
  max_leverage_steps: "e.g. 2027-03-31: 3.75x; 2027-12-31: 3.50x",
  min_interest_cover_steps: "e.g. 2027-03-31: 3.50x; 2027-12-31: 4.00x",
  min_liquidity_steps: "e.g. 2027-03-31: 60,000,000; 2027-12-31: 50,000,000",
  first_test_date: "YYYY-MM-DD, or first full quarter after 2026-02-15, year end December",
  reporting_days: "Days, e.g. 45",
  cure_period_days: "Days, e.g. 20",
  cure_day_basis: "calendar or business",
  cure_runs_from: "due date or delivery",
  springing_usage_percent: "Percentage, e.g. 35%",
  revolver_commitment: "Amount, e.g. 100,000,000",
};

export function displayValue(field: Pick<Field, "name" | "value">): string {
  switch (field.name) {
    case "monthly_payment":
    case "min_liquidity":
    case "revolver_commitment":
      return /^[\d,]+(\.\d+)?$/.test(field.value) ? formatAmount(field.value.replace(/,/g, "")) : field.value;
    case "rent_steps":
      return field.value
        .split(";")
        .map((step) => {
          const [when, amount = ""] = step.split(":").map((s) => s.trim());
          const from = /^\d+$/.test(when) ? `month ${when}` : /^\d{4}-\d{2}-\d{2}$/.test(when) ? formatDate(when) : when;
          return /^[\d,]+(\.\d+)?$/.test(amount) ? `${formatAmount(amount.replace(/,/g, ""))} from ${from}` : step.trim();
        })
        .join(" · ");
    case "free_periods":
      return field.value
        .split(",")
        .map((m) => {
          const month = m.trim();
          return /^\d{4}-\d{2}$/.test(month)
            ? new Date(`${month}-01T00:00:00Z`).toLocaleDateString("en-GB", { month: "short", year: "numeric", timeZone: "UTC" })
            : `month ${month}`;
        })
        .join(", ");
    case "reporting_days":
    case "cure_period_days":
      return /^\d+$/.test(field.value) ? `${field.value} days` : field.value;
    case "commencement_date":
    case "effective_date":
    case "terms_start_date":
    case "expiry_date":
    case "first_test_date":
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
  if (field.extractor === "preparer_input") return `Preparer input by ${field.entered_by}`;
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
      {props.doc.kind === "credit_agreement" && (
        <p className="term-note">
          Covenants are tested on the Covenants tab against the borrower&apos;s quarterly trial balances. A rejected
          covenant is treated as not in the agreement.
        </p>
      )}
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
          <span className="pill">
            {!optional ? "Not found" : doc.kind === "amendment" ? "Unchanged" : doc.kind === "lease" ? "None found" : "Not found"}
          </span>
        </div>
        {box ? (
          entryForm
        ) : (
          <>
            <div className="term-note">
              {MISSING_NOTES[name] ?? (!optional
                ? "The extractor could not find this term. Mark where the document states it."
                : doc.kind === "amendment"
                  ? "No change to this term was found, so the lease's value carries over. If the amendment does change it, mark where."
                  : doc.kind === "lease"
                    ? "None found, so the rent is treated as flat with no free months. If the lease has them, mark where."
                    : "Not found in the agreement, so it is not tested. If the agreement does have it, mark where.")}
            </div>
            {!INPUT_ONLY_FIELDS.has(name) && (
              <div className="term-actions">
                <button
                  className="btn btn-sm"
                  aria-pressed={drawingFor === name}
                  onClick={() => onStartDrawing(drawingFor === name ? null : name)}
                >
                  {drawingFor === name ? "Cancel marking" : "Mark on PDF"}
                </button>
              </div>
            )}
            {INPUT_FIELDS.has(name) && <PreparerInput doc={doc} name={name} onChanged={onChanged} />}
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
        <span>{field.page >= 1 ? `Page ${field.page}` : "Not stated in the document"}</span>
        <span>{source(field)}</span>
        {field.extractor !== "manual" && field.extractor !== "preparer_input" && (
          <span className="num">{Math.round(field.confidence * 100)}% confidence</span>
        )}
      </div>
      {field.basis && <div className="term-note">Basis: {field.basis}</div>}

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
          ) : INPUT_ONLY_FIELDS.has(name) ? null : (
            <div className="term-actions">
              <button className="btn btn-sm" onClick={() => onStartDrawing(drawingFor === name ? null : name)}>
                {drawingFor === name ? "Cancel marking" : "Mark the correct value on PDF"}
              </button>
            </div>
          )}
          {!box && INPUT_FIELDS.has(name) && <PreparerInput doc={doc} name={name} onChanged={onChanged} />}
        </div>
      )}
      {error && <div className="term-error">{error}</div>}
    </div>
  );
}

const MISSING_NOTES: Partial<Record<FieldName, string>> = {
  terms_start_date:
    "Not stated, so the new terms start on the agreed date. If they start later (for example when the current term ends), mark where.",
  expansion_treatment:
    "Not treated as an expansion. If this amendment adds space, record the IFRS 16.44 judgement as a preparer input.",
};

const INPUT_NOTES: Partial<Record<FieldName, string>> = {
  annual_discount_rate: "Contracts rarely state the lessee's incremental borrowing rate. Enter the rate you determined and how.",
  expansion_treatment:
    "If this amendment adds space, decide under IFRS 16.44: a separate lease when the added rent is commensurate with " +
    "its stand-alone price, otherwise a modification. Its monthly payment is then the added space's own rent.",
  cure_day_basis: "Whether the agreement counts the equity cure window in calendar or business days.",
  cure_runs_from: "Whether the cure window runs from the certificate's due date or from its delivery.",
  springing_usage_percent: "If leverage is tested only when the revolver is drawn above a share of the commitment, enter that share.",
  revolver_commitment: "The total revolving commitment the springing percentage is measured against.",
};

/** A term the contract does not state (e.g. the incremental borrowing rate): value plus how it was set. */
function PreparerInput({ doc, name, onChanged }: { doc: DocumentSummary; name: FieldName; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  const [basis, setBasis] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) {
    return (
      <div className="term-actions">
        <button className="btn btn-sm btn-ghost" type="button" onClick={() => setOpen(true)}>
          Not stated: enter as preparer input
        </button>
      </div>
    );
  }
  return (
    <form
      className="term-actions"
      style={{ flexDirection: "column", alignItems: "stretch" }}
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try {
          await api.enterInput(doc.id, name, value, basis);
          setOpen(false);
          onChanged();
        } catch (err) {
          setError((err as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      <div className="term-note" style={{ marginTop: 0 }}>
        {INPUT_NOTES[name] ?? "Enter the value you determined and how."} A partner reviews it like any other
        judgement.
      </div>
      <input className="input" value={value} placeholder={PLACEHOLDERS[name]} onChange={(e) => setValue(e.target.value)}
        aria-label={`${fieldLabel(name, doc.kind)} input value`} />
      <textarea className="input" rows={3} value={basis} onChange={(e) => setBasis(e.target.value)}
        placeholder="Basis, e.g. 5-year KES borrowing quote from the lessee's bank, adjusted for security and term"
        aria-label={`${fieldLabel(name, doc.kind)} basis`} />
      <div className="inline-form">
        <button className="btn btn-primary btn-sm" disabled={busy || !value.trim() || basis.trim().length < 10}>
          Save input
        </button>
        <button className="btn btn-sm" type="button" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
      {error && <div className="term-error">{error}</div>}
    </form>
  );
}
