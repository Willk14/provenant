"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { formatAmount } from "@/lib/format";
import { checksApi, type VarianceCheck } from "@/lib/tax";

const UPLOADS: Record<VarianceCheck["kind"], { title: string; columns: string; why: string }> = {
  vat: {
    title: "VAT turnover against revenue",
    columns: "period (YYYY-MM), standard_rated_sales, zero_rated_sales, exempt_sales",
    why: "Monthly VAT returns for the year, against revenue in the mapped trial balance.",
  },
  paye: {
    title: "PAYE gross pay against staff costs",
    columns: "period (YYYY-MM), gross_pay, paye",
    why: "Monthly PAYE (P10) returns, against employee costs. Employer NSSF, SHIF and pension are a usual gap.",
  },
  wht: {
    title: "Withholding tax certificates against supplier payments",
    columns: "certificate_number, withholdee_pin, withholdee_name, date, gross_amount, wht_amount",
    why: "Certificates the client issued, against what the purchase ledger (eTIMS tab) says each supplier was paid.",
  },
};

export function ChecksTab({ clientId, yearEnd }: { clientId: string; yearEnd: string }) {
  const [checks, setChecks] = useState<VarianceCheck[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setChecks(await checksApi.list(clientId, yearEnd));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [clientId, yearEnd]);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(action: () => Promise<VarianceCheck[]>) {
    setError(null);
    try {
      setChecks(await action());
    } catch (e) {
      setError((e as Error).message);
      throw e;
    }
  }

  if (!checks) return error ? <div className="error-banner">{error}</div> : <div className="empty">Loading…</div>;
  const open = checks.filter((c) => c.needs_explanation).length;

  return (
    <div className="records-main">
      <div className="card record-card">
        <h3>Compliance checks</h3>
        <p className="term-note">
          What the books say against what was declared to KRA. A gap is not an error: timing, exempt income and
          employer contributions explain many. Anything above the tolerance (the larger of 1,000 and 0.5% of the book
          figure) needs an explanation before the file is ready for review.
        </p>
        <div className="muted" style={{ fontSize: 13 }}>
          {open ? `${open} ${open === 1 ? "check needs" : "checks need"} an explanation.` : "No unexplained variances."}
        </div>
        {error && <div className="error-banner" style={{ marginTop: 10 }}>{error}</div>}
      </div>
      {checks.map((c) => (
        <CheckCard
          key={`${c.key}-${c.explanation ?? ""}`}
          check={c}
          onUpload={(file) => act(() => checksApi.upload(clientId, yearEnd, c.kind, file))}
          onExplain={(text) => act(() => checksApi.explain(clientId, yearEnd, c.key, text))}
        />
      ))}
    </div>
  );
}

function CheckCard({ check, onUpload, onExplain }: {
  check: VarianceCheck;
  onUpload: (file: File) => Promise<void>;
  onExplain: (text: string) => Promise<void>;
}) {
  const file = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState(check.explanation ?? "");
  const [busy, setBusy] = useState(false);
  const meta = UPLOADS[check.kind];
  const has = check.book !== null;
  const pill = !has
    ? null
    : check.needs_explanation
      ? { cls: "pill-bad", label: "Explain" }
      : check.explanation
        ? { cls: "pill-manual", label: "Explained" }
        : { cls: "pill-ok", label: "Agrees" };

  async function run(action: () => Promise<void>) {
    setBusy(true);
    try {
      await action();
    } catch {
      // the tab shows the error
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card record-card">
      <div className="record-head">
        <div>
          <h3>{meta.title}</h3>
          <p className="term-note">{meta.why}</p>
        </div>
        {pill && <span className={`pill ${pill.cls}`}>{pill.label}</span>}
      </div>
      <div className="term-actions">
        <span className="faint" style={{ fontSize: 11 }}>CSV: {meta.columns}</span>
        <input ref={file} type="file" accept=".csv,text/csv" aria-label={`${meta.title} CSV`} />
        <button
          className="btn btn-sm"
          type="button"
          disabled={busy}
          onClick={() => {
            const f = file.current?.files?.[0];
            if (f) void run(() => onUpload(f).then(() => { if (file.current) file.current.value = ""; }));
          }}
        >
          {check.filename ? "Replace" : "Upload"}
        </button>
        {check.filename && <span className="faint" style={{ fontSize: 12 }}>{check.filename} · {check.uploaded_by}</span>}
      </div>

      {has && (
        <table className="table comp-table" style={{ marginTop: 10 }}>
          <tbody>
            <tr>
              <td>
                {check.book_label}
                {check.book_accounts.length > 0 && (
                  <div className="faint" style={{ fontSize: 11 }}>accounts {check.book_accounts.join(", ")}</div>
                )}
              </td>
              <td className="num">{formatAmount(check.book ?? "0")}</td>
            </tr>
            <tr>
              <td>{check.declared_label}</td>
              <td className="num">{formatAmount(check.declared ?? "0")}</td>
            </tr>
            <tr data-total>
              <td>Variance (tolerance {formatAmount(check.tolerance ?? "0")})</td>
              <td className="num">{formatAmount(check.variance ?? "0")}</td>
            </tr>
          </tbody>
        </table>
      )}
      {check.notes.map((n) => (
        <div key={n} className="callout callout-warn" style={{ padding: "6px 10px", fontSize: 13, marginTop: 8 }}>{n}</div>
      ))}
      {check.key === "wht" && check.rows.length > 0 && (
        <table className="table register-table">
          <thead>
            <tr><th>Supplier</th><th className="num">Paid</th><th className="num">Certified</th><th className="num">Variance</th></tr>
          </thead>
          <tbody>
            {check.rows.map((r) => (
              <tr key={r.label}>
                <td>{r.label}</td>
                <td className="num">{formatAmount(r.book)}</td>
                <td className="num">{formatAmount(r.declared)}</td>
                <td className="num">{formatAmount(r.variance)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {has && (check.needs_explanation || check.explanation) && (
        <form
          className="term-actions"
          style={{ flexDirection: "column", alignItems: "stretch" }}
          onSubmit={(e) => {
            e.preventDefault();
            void run(() => onExplain(draft));
          }}
        >
          <textarea
            className="input"
            rows={2}
            value={draft}
            aria-label={`Explanation for ${meta.title}`}
            placeholder="What makes up the difference, e.g. December invoices declared in January"
            onChange={(e) => setDraft(e.target.value)}
          />
          <div className="inline-form">
            <button className="btn btn-sm btn-primary" disabled={busy || draft === (check.explanation ?? "")}>
              Save explanation
            </button>
            {check.explained_by && <span className="faint" style={{ fontSize: 12 }}>by {check.explained_by}</span>}
          </div>
        </form>
      )}
    </div>
  );
}
