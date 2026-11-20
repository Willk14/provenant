"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";

import { formatAmount, formatDate } from "@/lib/format";
import { ledgerApi, type JournalLine, type JournalTest, type Ledger } from "@/lib/tax";

export function LedgerTab({ clientId, yearEnd }: { clientId: string; yearEnd: string }) {
  const [ledger, setLedger] = useState<Ledger | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setLedger(await ledgerApi.get(clientId, yearEnd));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [clientId, yearEnd]);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(action: () => Promise<Ledger>) {
    setBusy(true);
    setError(null);
    try {
      setLedger(await action());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!ledger) return error ? <div className="error-banner">{error}</div> : <div className="empty">Loading…</div>;
  const test = ledger.tests.find((t) => t.key === selected) ?? null;
  const differences = ledger.reconciliation.filter((r) => Number(r.difference) !== 0);

  return (
    <div className="records-main">
      <div className="card record-card">
        <h3>General ledger: journal entry tests</h3>
        <p className="term-note">
          Upload the year&apos;s general ledger (CSV: entry_id, date, account_code, and amount or debit and credit;
          optional posted_at, description, user, source). Every test runs over the whole population, not a sample. A
          flag selects an entry for review; it does not say the entry is wrong.
        </p>
        <UploadForm busy={busy} onUpload={(file, limit) => act(() => ledgerApi.upload(clientId, yearEnd, file, limit))} />
        {error && <div className="error-banner" style={{ marginTop: 10 }}>{error}</div>}
        {ledger.uploaded && (
          <div className="faint" style={{ fontSize: 12, marginTop: 8 }}>
            {ledger.filename} · {ledger.uploaded_by} · {ledger.lines.toLocaleString()} lines in{" "}
            {ledger.entries.toLocaleString()} entries · debits {formatAmount(ledger.debits)}
            {ledger.first_date && ledger.last_date && ` · ${formatDate(ledger.first_date)} – ${formatDate(ledger.last_date)}`}
            {" "}· tests {ledger.test_version}
          </div>
        )}
      </div>

      {ledger.uploaded && (
        <>
          <div className="ledger-tests">
            {ledger.tests.map((t) => (
              <button key={t.key} type="button" className="card ledger-test" aria-pressed={selected === t.key}
                disabled={!t.applies} onClick={() => setSelected(selected === t.key ? null : t.key)}>
                <div className="term-label">{t.title}</div>
                {t.applies ? (
                  <>
                    <div className="ledger-count">{t.count.toLocaleString()}</div>
                    <div className="faint" style={{ fontSize: 12 }}>
                      {formatAmount(t.value)}{t.count > 0 && ` · ${t.open} open`}
                    </div>
                  </>
                ) : (
                  <div className="faint" style={{ fontSize: 12 }}>Not run: {t.description}</div>
                )}
              </button>
            ))}
          </div>

          {test && (
            <TestDetail test={test} clientId={clientId} yearEnd={yearEnd} busy={busy}
              onDecide={(body) => act(() => ledgerApi.decide(clientId, yearEnd, { ...body, test: test.key }))} />
          )}

          <div className="record-layout-2">
            <div className="card record-card">
              <h3>Entries flagged by the most tests</h3>
              <table className="table register-table">
                <thead><tr><th>Entry</th><th>Tests</th><th className="num">Value</th></tr></thead>
                <tbody>
                  {ledger.risk.slice(0, 25).map((r) => (
                    <tr key={r.entry_id}>
                      <td className="mono">{r.entry_id}</td>
                      <td>
                        {r.tests.map((k) => ledger.tests.find((t) => t.key === k)?.title ?? k).join(", ")}
                        {r.cleared > 0 && <div className="faint" style={{ fontSize: 11 }}>{r.cleared} cleared</div>}
                      </td>
                      <td className="num">{formatAmount(r.value)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="card record-card">
              <h3>Benford&apos;s law (first digits)</h3>
              {ledger.benford ? (
                <>
                  <p className="term-note">
                    {ledger.benford.lines.toLocaleString()} lines of 10 or more · mean absolute deviation{" "}
                    {ledger.benford.mad} · <strong>{ledger.benford.conformity}</strong>
                  </p>
                  <div className="benford" role="img" aria-label="Observed against expected first-digit frequencies">
                    {ledger.benford.observed.map((o, i) => (
                      <div key={i} className="benford-col">
                        <div className="benford-bars">
                          <div className="benford-bar" style={{ height: `${o * 300}%` }} title={`${(o * 100).toFixed(1)}% observed`} />
                          <div className="benford-expected" style={{ bottom: `${ledger.benford!.expected[i] * 300}%` }}
                            title={`${(ledger.benford!.expected[i] * 100).toFixed(1)}% expected`} />
                        </div>
                        <div className="faint" style={{ fontSize: 11 }}>{i + 1}</div>
                      </div>
                    ))}
                  </div>
                </>
              ) : (
                <p className="term-note">Fewer than 100 lines of 10 or more: too few for a first-digit test.</p>
              )}
              <h3 style={{ marginTop: 16 }}>Profit or loss accounts against the trial balance</h3>
              {differences.length === 0 ? (
                <p className="term-note">
                  {ledger.reconciliation.length
                    ? "Every profit or loss account agrees with the year's ledger movement."
                    : "No mapped profit or loss accounts to compare yet."}
                </p>
              ) : (
                <table className="table register-table">
                  <thead><tr><th>Account</th><th className="num">Ledger</th><th className="num">Trial balance</th><th className="num">Difference</th></tr></thead>
                  <tbody>
                    {differences.map((r) => (
                      <tr key={r.account_code}>
                        <td className="mono">{r.account_code}</td>
                        <td className="num">{formatAmount(r.ledger)}</td>
                        <td className="num">{formatAmount(r.trial_balance)}</td>
                        <td className="num">{formatAmount(r.difference)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function UploadForm({ busy, onUpload }: { busy: boolean; onUpload: (file: File, limit: string) => Promise<void> }) {
  const file = useRef<HTMLInputElement>(null);
  const [limit, setLimit] = useState("");
  return (
    <form className="term-actions" onSubmit={(e) => {
      e.preventDefault();
      const f = file.current?.files?.[0];
      if (f) void onUpload(f, limit).then(() => { if (file.current) file.current.value = ""; });
    }}>
      <input ref={file} type="file" accept=".csv,text/csv" aria-label="General ledger CSV" />
      <input className="input" value={limit} onChange={(e) => setLimit(e.target.value)} style={{ width: 170 }}
        placeholder="Approval limit (optional)" aria-label="Approval limit" />
      <button className="btn btn-sm" disabled={busy}>{busy ? "Working…" : "Upload and test"}</button>
    </form>
  );
}

function TestDetail({ test, clientId, yearEnd, busy, onDecide }: {
  test: JournalTest;
  clientId: string;
  yearEnd: string;
  busy: boolean;
  onDecide: (body: { entry_id: string; status: "cleared" | "escalated" | "open"; reason?: string }) => Promise<void>;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const [lines, setLines] = useState<JournalLine[]>([]);
  const [reason, setReason] = useState("");

  async function show(entryId: string) {
    if (open === entryId) return setOpen(null);
    setOpen(entryId);
    setReason("");
    setLines(await ledgerApi.entry(clientId, yearEnd, entryId));
  }

  return (
    <div className="card record-card">
      <h3>{test.title}</h3>
      <p className="term-note">
        {test.description} {test.count > test.sample.length && `Showing the ${test.sample.length} largest of ${test.count.toLocaleString()}.`}
      </p>
      <table className="table">
        <thead><tr><th>Entry</th><th>Why</th><th className="num">Value</th><th>Decision</th></tr></thead>
        <tbody>
          {test.sample.map((f) => (
            <Fragment key={f.entry_id}>
              <tr>
                <td>
                  <button className="link attachment-name mono" type="button" onClick={() => void show(f.entry_id)}>
                    {f.entry_id}
                  </button>
                </td>
                <td>{f.detail}</td>
                <td className="num">{formatAmount(f.value)}</td>
                <td>
                  {f.decision ? (
                    <>
                      <span className={`pill ${f.decision.status === "cleared" ? "pill-ok" : "pill-bad"}`}>
                        {f.decision.status === "cleared" ? "Cleared" : "Escalated"}
                      </span>
                      <div className="faint" style={{ fontSize: 11 }}>{f.decision.reason} · {f.decision.decided_by}</div>
                    </>
                  ) : (
                    <span className="pill pill-warn">Open</span>
                  )}
                </td>
              </tr>
              {open === f.entry_id && (
                <tr>
                  <td colSpan={4}>
                    <table className="table register-table">
                      <tbody>
                        {lines.map((l) => (
                          <tr key={l.row}>
                            <td>{formatDate(l.date)}{l.posted_at && <div className="faint" style={{ fontSize: 11 }}>{l.posted_at.replace("T", " ").slice(0, 16)}</div>}</td>
                            <td className="mono">{l.account_code}</td>
                            <td>{l.description}<div className="faint" style={{ fontSize: 11 }}>row {l.row}{l.user && ` · ${l.user}`}{l.source && ` · ${l.source}`}</div></td>
                            <td className="num">{formatAmount(l.amount)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <div className="inline-form" style={{ marginTop: 6 }}>
                      <input className="input" value={reason} onChange={(e) => setReason(e.target.value)}
                        placeholder="What was checked, or why it is escalated" aria-label={`Reason for ${f.entry_id}`} />
                      <button className="btn btn-sm btn-primary" type="button" disabled={busy || reason.trim().length < 10}
                        onClick={() => void onDecide({ entry_id: f.entry_id, status: "cleared", reason })}>Clear</button>
                      <button className="btn btn-sm" type="button" disabled={busy || reason.trim().length < 10}
                        onClick={() => void onDecide({ entry_id: f.entry_id, status: "escalated", reason })}>Escalate</button>
                      {f.decision && (
                        <button className="btn btn-sm btn-ghost" type="button" disabled={busy}
                          onClick={() => void onDecide({ entry_id: f.entry_id, status: "open" })}>Reopen</button>
                      )}
                    </div>
                    <p className="term-note">Clearing needs a reviewer or partner who did not upload the ledger.</p>
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}
