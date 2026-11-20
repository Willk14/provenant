"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { formatAmount, formatDate } from "@/lib/format";
import { etimsApi, type EtimsLine, type EtimsReconciliation, type EtimsStatus } from "@/lib/tax";

interface Props {
  clientId: string;
  yearEnd: string;
  onChanged: () => void;
  onOpenComputation: () => void;
}

const STATUS: Record<EtimsStatus, { label: string; pill: string }> = {
  unmatched: { label: "No eTIMS invoice", pill: "pill-bad" },
  differs: { label: "Differs", pill: "pill-bad" },
  probable: { label: "Probable: confirm", pill: "pill-warn" },
  matched: { label: "Matched", pill: "pill-ok" },
  exempt: { label: "Exempt", pill: "pill-manual" },
};
const ORDER: EtimsStatus[] = ["unmatched", "differs", "probable", "exempt", "matched"];

export function EtimsTab({ clientId, yearEnd, onChanged, onOpenComputation }: Props) {
  const [rec, setRec] = useState<EtimsReconciliation | null>(null);
  const [filter, setFilter] = useState<EtimsStatus | "all">("all");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setRec(await etimsApi.get(clientId, yearEnd));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [clientId, yearEnd]);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(action: () => Promise<EtimsReconciliation>) {
    setBusy(true);
    setError(null);
    try {
      setRec(await action());
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!rec) return error ? <div className="error-banner">{error}</div> : <div className="empty">Loading…</div>;

  const ready = rec.ledger && rec.export;
  const shown = rec.lines.filter((l) => filter === "all" || l.status === filter);
  const addback = rec.totals.addback ?? "0";

  return (
    <div className="records-main">
      <div className="card record-card">
        <h3>eTIMS reconciliation</h3>
        <p className="term-note">
          Match the purchase ledger to the client&apos;s eTIMS purchase invoices before a desk audit. Purchases posted to
          deductible accounts with no eTIMS invoice, or above the invoice, become an add-back once you apply it. Confirm
          the provision and its exemptions for the year: this is working paper support, not tax advice.
        </p>
        <div className="record-grid" style={{ gridTemplateColumns: "1fr 1fr", gap: "0 16px" }}>
          <Upload
            title="Purchase ledger"
            hint="date, supplier, supplier_pin, invoice_number, account_code, amount (excl. VAT); optional etims_number, description"
            current={rec.ledger}
            onUpload={(f) => act(() => etimsApi.uploadLedger(clientId, yearEnd, f))}
            busy={busy}
          />
          <Upload
            title="eTIMS purchase invoices"
            hint="cu_invoice_number, supplier_pin, invoice_number, date, taxable_value; optional supplier_name, vat"
            current={rec.export}
            onUpload={(f) => act(() => etimsApi.uploadInvoices(clientId, yearEnd, f))}
            busy={busy}
          />
        </div>
        {error && <div className="error-banner" style={{ marginTop: 10 }}>{error}</div>}
      </div>

      {ready && (
        <>
          <div className={`save-bar${rec.applied_by ? "" : " save-bar-dirty"}`}>
            <div>
              <strong>Add-back: {formatAmount(addback)}</strong>
              <div className="muted" style={{ fontSize: 13 }}>
                {rec.probable > 0
                  ? `${rec.probable} probable ${rec.probable === 1 ? "match needs" : "matches need"} a decision before it can be applied.`
                  : rec.applied_by
                    ? `Applied to the tax computation by ${rec.applied_by}.`
                    : "Not in the tax computation until you apply it."}
              </div>
            </div>
            <div className="term-actions" style={{ marginTop: 0 }}>
              {rec.applied_by && (
                <button className="btn btn-sm" type="button" onClick={onOpenComputation}>View computation</button>
              )}
              <button className={`btn btn-sm${rec.applied_by ? "" : " btn-primary"}`} type="button"
                disabled={busy || (!rec.applied_by && rec.probable > 0)}
                onClick={() => act(() => etimsApi.apply(clientId, yearEnd, !rec.applied_by))}>
                {rec.applied_by ? "Remove from computation" : "Apply to computation"}
              </button>
            </div>
          </div>

          <div className="chips" role="group" aria-label="Filter by status">
            <button type="button" className="chip" aria-pressed={filter === "all"} onClick={() => setFilter("all")}>
              <b>All</b> {rec.lines.length}
            </button>
            {ORDER.map((status) => {
              const count = rec.lines.filter((l) => l.status === status).length;
              return count ? (
                <button key={status} type="button" className="chip" aria-pressed={filter === status}
                  onClick={() => setFilter(status)}>
                  <b>{STATUS[status].label}</b> {count} · {formatAmount(rec.totals[status] ?? "0")}
                </button>
              ) : null;
            })}
          </div>

          <div className="card" style={{ overflowX: "auto" }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Purchase</th>
                  <th>Account</th>
                  <th className="num">Amount</th>
                  <th>eTIMS</th>
                  <th>Status</th>
                  <th aria-label="Decision" />
                </tr>
              </thead>
              <tbody>
                {shown.map((line) => (
                  <Row key={line.key} line={line} busy={busy}
                    onDecide={(body) => act(() => etimsApi.decide(clientId, yearEnd, { line_key: line.key, ...body }))} />
                ))}
              </tbody>
            </table>
          </div>

          {rec.etims_only.length > 0 && (
            <div className="card record-card">
              <h3>In eTIMS but not in the ledger ({rec.etims_only.length})</h3>
              <p className="term-note">Unrecorded purchases, or invoices issued to the client in error. Worth a question to the client.</p>
              <table className="table register-table">
                <tbody>
                  {rec.etims_only.map((i) => (
                    <tr key={i.cu_invoice_number}>
                      <td>
                        <span className="mono">{i.cu_invoice_number}</span> {i.supplier_name || i.supplier_pin}
                        <div className="faint" style={{ fontSize: 11 }}>{i.invoice_number} · {formatDate(i.date)}</div>
                      </td>
                      <td className="num">{formatAmount(i.taxable_value)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function Upload({ title, hint, current, onUpload, busy }: {
  title: string;
  hint: string;
  current: { filename: string; uploaded_by: string } | null;
  onUpload: (file: File) => Promise<void>;
  busy: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <div className="field">
      <span>{title}</span>
      <div className="faint" style={{ fontSize: 11 }}>CSV: {hint}</div>
      {current && <div style={{ fontSize: 12 }}>{current.filename} · {current.uploaded_by}</div>}
      <div className="term-actions" style={{ marginTop: 4 }}>
        <input ref={input} type="file" accept=".csv,text/csv" aria-label={`${title} CSV`} />
        <button className="btn btn-sm" type="button" disabled={busy}
          onClick={() => {
            const file = input.current?.files?.[0];
            if (file) void onUpload(file).then(() => { if (input.current) input.current.value = ""; });
          }}>
          {current ? "Replace" : "Upload"}
        </button>
      </div>
    </div>
  );
}

function Row({ line, busy, onDecide }: {
  line: EtimsLine;
  busy: boolean;
  onDecide: (body: { kind: "exempt" | "match" | "clear"; reason?: string; cu_invoice_number?: string }) => Promise<void>;
}) {
  const [exempting, setExempting] = useState(false);
  const [reason, setReason] = useState("");
  const status = STATUS[line.status];
  return (
    <tr>
      <td>
        {line.supplier} <span className="mono faint">{line.invoice_number}</span>
        <div className="faint" style={{ fontSize: 11 }}>{formatDate(line.date)} · {line.supplier_pin}</div>
      </td>
      <td>
        <span className="mono">{line.account_code}</span>
        {!line.in_addback && <div className="faint" style={{ fontSize: 11 }}>not a deductible account</div>}
      </td>
      <td className="num">
        {formatAmount(line.amount)}
        {line.unsupported !== "0" && line.unsupported !== "0.00" && line.in_addback && (
          <div className="faint" style={{ fontSize: 11 }}>unsupported {formatAmount(line.unsupported)}</div>
        )}
      </td>
      <td>
        {line.cu_invoice_number ? (
          <>
            <span className="mono">{line.cu_invoice_number}</span>
            {line.etims_amount && <div className="faint" style={{ fontSize: 11 }}>{formatAmount(line.etims_amount)}</div>}
          </>
        ) : (
          <span className="faint">none</span>
        )}
      </td>
      <td>
        <span className={`pill ${status.pill}`}>{status.label}</span>
        <div className="faint" style={{ fontSize: 11 }}>
          {line.note}
          {line.decided_by && ` · ${line.decided_by}`}
        </div>
      </td>
      <td>
        {exempting ? (
          <form className="inline-form" onSubmit={(e) => {
            e.preventDefault();
            void onDecide({ kind: "exempt", reason }).then(() => setExempting(false));
          }}>
            <input className="input" autoFocus value={reason} placeholder="Why no eTIMS invoice is needed"
              aria-label="Exemption reason" onChange={(e) => setReason(e.target.value)} />
            <button className="btn btn-sm btn-primary" disabled={busy || reason.trim().length < 5}>Save</button>
            <button className="btn btn-sm" type="button" onClick={() => setExempting(false)}>Cancel</button>
          </form>
        ) : (
          <div className="term-actions" style={{ marginTop: 0 }}>
            {line.status === "probable" && line.cu_invoice_number && (
              <button className="btn btn-sm" type="button" disabled={busy}
                onClick={() => void onDecide({ kind: "match", cu_invoice_number: line.cu_invoice_number ?? "" })}>
                Confirm match
              </button>
            )}
            {(line.status === "unmatched" || line.status === "differs" || line.status === "probable") && (
              <button className="btn btn-sm btn-ghost" type="button" onClick={() => setExempting(true)}>Exempt…</button>
            )}
            {line.decided_by && (
              <button className="btn btn-sm btn-ghost" type="button" disabled={busy}
                onClick={() => void onDecide({ kind: "clear" })}>
                Undo
              </button>
            )}
          </div>
        )}
      </td>
    </tr>
  );
}
