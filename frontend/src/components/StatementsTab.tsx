"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { formatAmount, formatDate } from "@/lib/format";
import { statementsApi, type StatementsView, type StatementTxn } from "@/lib/tax";

const SHOW = 500; // rows rendered at once; filters narrow the rest

export function StatementsTab({ clientId, yearEnd }: { clientId: string; yearEnd: string }) {
  const [view, setView] = useState<StatementsView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<StatementTxn["status"] | "all">("all");
  const [category, setCategory] = useState<string>("all");

  const load = useCallback(async () => {
    try {
      setView(await statementsApi.get(clientId, yearEnd));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [clientId, yearEnd]);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(action: () => Promise<StatementsView>) {
    setBusy(true);
    setError(null);
    try {
      setView(await action());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!view) return error ? <div className="error-banner">{error}</div> : <div className="empty">Loading…</div>;

  const label = (key: string | null) => (key ? view.categories[key] ?? key : "Unassigned");
  const rows = view.transactions.filter(
    (t) => (status === "all" || t.status === status) && (category === "all" || (t.category ?? "unassigned") === category),
  );
  const suggested = view.transactions.filter((t) => t.status === "suggested").length;
  const unassigned = view.transactions.filter((t) => t.status === "unassigned").length;

  return (
    <div className="records-main">
      <div className="card record-card">
        <h3>M-Pesa and bank statements</h3>
        <p className="term-note">
          Upload the M-Pesa business statement (the portal&apos;s CSV export) and bank statements for the year. Running
          balances are checked row by row, duplicates across uploads are dropped, sweeps between the client&apos;s own
          accounts are paired, and each movement gets a suggested category for you to confirm.
        </p>
        <UploadForm busy={busy} onUpload={(kind, name, file) => act(() => statementsApi.upload(clientId, yearEnd, kind, name, file))} />
        {error && <div className="error-banner" style={{ marginTop: 10 }}>{error}</div>}
        {view.statements.length > 0 && (
          <table className="table register-table">
            <thead>
              <tr><th>Account</th><th>Period</th><th className="num">Movements</th><th className="num">In</th><th className="num">Out</th><th aria-label="Remove" /></tr>
            </thead>
            <tbody>
              {view.statements.map((x) => (
                <tr key={x.id}>
                  <td>
                    {x.label}
                    <div className="faint" style={{ fontSize: 11 }}>{x.kind === "mpesa" ? "M-Pesa" : "Bank"} · {x.filename} · {x.uploaded_by}</div>
                  </td>
                  <td>{x.first_date && x.last_date ? `${formatDate(x.first_date)} – ${formatDate(x.last_date)}` : "–"}</td>
                  <td className="num">{x.movements}</td>
                  <td className="num">{formatAmount(x.money_in)}</td>
                  <td className="num">{formatAmount(x.money_out)}</td>
                  <td>
                    <button className="btn btn-sm btn-ghost btn-danger" type="button" disabled={busy}
                      onClick={() => act(() => statementsApi.remove(clientId, yearEnd, x.id))}>Remove</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {view.statements.length > 0 && (
        <>
          {view.breaks.map((b) => (
            <div key={`${b.source}-${b.row}`} className="callout callout-warn" style={{ fontSize: 13 }}>
              <strong>Balance break in {b.source}, row {b.row}</strong>
              Expected {formatAmount(b.expected)} from the row before, statement says {formatAmount(b.stated)} (gap{" "}
              {formatAmount(b.gap)}): a missing or altered row.
            </div>
          ))}
          <div className="muted" style={{ fontSize: 13 }}>
            {view.duplicates} duplicate {view.duplicates === 1 ? "row" : "rows"} dropped · {view.transfers} own-account{" "}
            {view.transfers === 1 ? "transfer" : "transfers"} paired · {unassigned} unassigned
          </div>

          <div className="card record-card">
            <div className="record-head">
              <h3>By category</h3>
              <button className="btn btn-sm btn-primary" type="button" disabled={busy || !suggested}
                onClick={() => act(() => statementsApi.confirmSuggestions(clientId, yearEnd))}>
                Confirm {suggested} suggestions
              </button>
            </div>
            <table className="table register-table">
              <thead><tr><th>Category</th><th className="num">Money in</th><th className="num">Money out</th></tr></thead>
              <tbody>
                {Object.entries(view.totals).map(([key, [money_in, money_out]]) => (
                  <tr key={key}>
                    <td>
                      <button className="link attachment-name" type="button" onClick={() => setCategory(key)}>
                        {label(key === "unassigned" ? null : key)}
                      </button>
                    </td>
                    <td className="num">{formatAmount(money_in)}</td>
                    <td className="num">{formatAmount(money_out)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {view.revenue !== null && (
              <p className="term-note">
                Confirmed sales receipts {formatAmount(view.confirmed_sales_receipts)} against revenue in the trial
                balance {formatAmount(view.revenue)}. Credit sales, cash sales and receipts into other accounts explain
                a gap; a large one is worth a question.
              </p>
            )}
          </div>

          <div className="chips" role="group" aria-label="Filter movements">
            {(["all", "unassigned", "suggested", "confirmed"] as const).map((s) => (
              <button key={s} type="button" className="chip" aria-pressed={status === s} onClick={() => setStatus(s)}>
                <b>{s === "all" ? "All" : s[0].toUpperCase() + s.slice(1)}</b>
              </button>
            ))}
            {category !== "all" && (
              <button type="button" className="chip" aria-pressed onClick={() => setCategory("all")}>
                <b>{label(category === "unassigned" ? null : category)}</b> ×
              </button>
            )}
          </div>
          <div className="card" style={{ overflowX: "auto" }}>
            <table className="table">
              <thead>
                <tr><th>Date</th><th>Movement</th><th className="num">Amount</th><th>Category</th></tr>
              </thead>
              <tbody>
                {rows.slice(0, SHOW).map((t) => (
                  <tr key={t.key}>
                    <td>{formatDate(t.date)}</td>
                    <td>
                      {t.description}
                      <div className="faint" style={{ fontSize: 11 }}>
                        {t.source} · row {t.row}{t.reference && ` · ${t.reference}`}{t.counterparty && ` · ${t.counterparty}`}
                      </div>
                    </td>
                    <td className="num">{formatAmount(t.amount)}</td>
                    <td>
                      <select className="input" aria-label={`Category for ${t.reference || t.description}`} disabled={busy}
                        value={t.category ?? ""}
                        onChange={(e) => act(() => statementsApi.categorise(clientId, yearEnd, [t.key], e.target.value || null))}>
                        <option value="">Unassigned</option>
                        {Object.entries(view.categories).map(([key, name]) => (
                          <option key={key} value={key}>{name}</option>
                        ))}
                      </select>
                      <div className="faint" style={{ fontSize: 11 }}>
                        {t.status === "confirmed" ? `confirmed · ${t.rule}` : t.status === "suggested" ? `suggested · ${t.rule}` : ""}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rows.length > SHOW && <p className="term-note">Showing {SHOW} of {rows.length}: filter to narrow.</p>}
          </div>
        </>
      )}
    </div>
  );
}

function UploadForm({ busy, onUpload }: {
  busy: boolean;
  onUpload: (kind: "mpesa" | "bank", label: string, file: File) => Promise<void>;
}) {
  const file = useRef<HTMLInputElement>(null);
  const [kind, setKind] = useState<"mpesa" | "bank">("mpesa");
  const [name, setName] = useState("");
  return (
    <form className="term-actions" onSubmit={(e) => {
      e.preventDefault();
      const f = file.current?.files?.[0];
      if (f) void onUpload(kind, name, f).then(() => { if (file.current) file.current.value = ""; });
    }}>
      <select className="input" value={kind} aria-label="Statement type" onChange={(e) => setKind(e.target.value as "mpesa" | "bank")}>
        <option value="mpesa">M-Pesa statement</option>
        <option value="bank">Bank statement</option>
      </select>
      <input className="input" value={name} aria-label="Account name" onChange={(e) => setName(e.target.value)}
        placeholder={kind === "mpesa" ? "e.g. M-Pesa Till 123456" : "e.g. Equity Bank 0123…"} />
      <input ref={file} type="file" accept=".csv,text/csv" aria-label="Statement CSV" />
      <button className="btn btn-sm" disabled={busy || name.trim().length < 3}>Upload</button>
    </form>
  );
}
