"use client";

import { useCallback, useEffect, useState } from "react";

import { ReconciliationExplain } from "@/components/ClaudeAssist";
import { formatAmount, formatDate } from "@/lib/format";
import { practiceApi, type Reconciliation, type TrustAccount, type TrustEntry } from "@/lib/practice";
import { usePersona } from "@/lib/usePersona";

export default function TrustPage() {
  const persona = usePersona();
  const [accounts, setAccounts] = useState<TrustAccount[]>([]);
  const [accountId, setAccountId] = useState("");
  const [entries, setEntries] = useState<TrustEntry[]>([]);
  const [recs, setRecs] = useState<Reconciliation[]>([]);
  const [newAccount, setNewAccount] = useState({ name: "", bank: "", days: "", basis: "" });
  const [rec, setRec] = useState({ asOf: "", balance: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const list = await practiceApi.trustAccounts();
      setAccounts(list);
      const current = accountId || list[0]?.id || "";
      setAccountId(current);
      if (current) {
        const [e, r] = await Promise.all([practiceApi.trustEntries(current), practiceApi.reconciliations(current)]);
        setEntries(e);
        setRecs(r);
      }
    } catch (e) {
      setError((e as Error).message);
    }
  }, [accountId]);

  useEffect(() => {
    if (persona) void load();
  }, [persona, load]);

  async function act(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const partner = !!persona?.roles.includes("partner");
  const account = accounts.find((a) => a.id === accountId);
  const latest = recs[0];
  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Client trust account</h1>
          <p className="muted">
            Three-way reconciliation of the bank, the trust cashbook and every client ledger, done by rules, not by
            AI. Provenant never moves money: payments are made at the bank by a person.
          </p>
        </div>
        {accounts.length > 1 && (
          <select className="input" value={accountId} onChange={(e) => setAccountId(e.target.value)} aria-label="Trust account">
            {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        )}
      </div>
      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="callout callout-ok">{notice}</div>}

      {partner && (
        <form className="card practice-form" onSubmit={(e) => {
          e.preventDefault();
          void act(async () => {
            const made = await practiceApi.createTrustAccount({ name: newAccount.name, bank: newAccount.bank,
              dormant_days: newAccount.days ? Number(newAccount.days) : null, dormant_basis: newAccount.basis || null });
            setAccountId(made.id);
            setNewAccount({ name: "", bank: "", days: "", basis: "" });
          });
        }}>
          <strong>Add a trust account</strong>
          <div className="practice-row">
            <input className="input grow" required placeholder="Name, e.g. Client account" value={newAccount.name}
              onChange={(e) => setNewAccount({ ...newAccount, name: e.target.value })} aria-label="Account name" />
            <input className="input" required placeholder="Bank" value={newAccount.bank}
              onChange={(e) => setNewAccount({ ...newAccount, bank: e.target.value })} aria-label="Bank" />
            <input className="input" type="number" min={30} placeholder="Dormant after (days)" value={newAccount.days}
              onChange={(e) => setNewAccount({ ...newAccount, days: e.target.value })} aria-label="Dormant after days" />
            <input className="input grow" placeholder="Basis for the dormancy period" value={newAccount.basis}
              onChange={(e) => setNewAccount({ ...newAccount, basis: e.target.value })} aria-label="Dormancy basis" />
            <button className="btn btn-sm" disabled={busy}>Add</button>
          </div>
        </form>
      )}

      {!account ? <div className="card"><div className="empty">No trust account yet. A partner adds one above.</div></div> : (
        <>
          <section className="card practice-form">
            <div className="practice-row space">
              <strong>{account.name} · {account.bank}</strong>
              <span className="num">Cashbook {formatAmount(account.cashbook)}</span>
            </div>
            <form className="practice-row" onSubmit={(e) => {
              e.preventDefault();
              const file = (e.currentTarget.elements.namedItem("cashbook") as HTMLInputElement).files?.[0];
              if (file) void act(async () => setNotice(`${(await practiceApi.importCashbook(account.id, file)).imported} cashbook lines imported.`));
            }}>
              <span className="faint" style={{ fontSize: 12 }}>Cashbook CSV: date, amount (receipts positive), reference, description, matter</span>
              <input name="cashbook" type="file" accept=".csv,text/csv" required aria-label="Cashbook CSV" />
              <button className="btn btn-sm" disabled={busy}>Import cashbook</button>
            </form>
            <form className="practice-row" onSubmit={(e) => {
              e.preventDefault();
              const file = (e.currentTarget.elements.namedItem("statement") as HTMLInputElement).files?.[0];
              if (file) void act(async () => { await practiceApi.reconcile(account.id, rec.asOf, rec.balance, file); });
            }}>
              <span className="faint" style={{ fontSize: 12 }}>Bank statement CSV and its closing balance</span>
              <input className="input" type="date" required value={rec.asOf} onChange={(e) => setRec({ ...rec, asOf: e.target.value })} aria-label="Reconcile as of" />
              <input className="input" required placeholder="Closing balance" value={rec.balance} onChange={(e) => setRec({ ...rec, balance: e.target.value })} aria-label="Closing bank balance" />
              <input name="statement" type="file" accept=".csv,text/csv" required aria-label="Bank statement CSV" />
              <button className="btn btn-primary btn-sm" disabled={busy}>Reconcile</button>
            </form>
          </section>

          {latest && (
            <section className="card practice-form">
              <div className="practice-row space">
                <strong>Reconciliation at {formatDate(latest.as_of)}</strong>
                <span className={`pill ${latest.balanced ? "pill-ok" : "pill-bad"}`}>{latest.balanced ? "Balanced" : "Breaks to resolve"}</span>
              </div>
              <div className="trust-three">
                <div><span className="faint">Adjusted bank</span><b className="num">{formatAmount(latest.workpaper.adjusted_bank)}</b>
                  <span className="faint">statement {formatAmount(latest.workpaper.bank_balance)}</span></div>
                <div><span className="faint">Cashbook</span><b className="num">{formatAmount(latest.workpaper.cashbook)}</b></div>
                <div><span className="faint">Client ledgers</span><b className="num">{formatAmount(latest.workpaper.ledgers_total)}</b></div>
              </div>
              {latest.workpaper.alerts.length > 0 && <ul className="practice-list">{latest.workpaper.alerts.map((a, i) => (
                <li key={i}><span className={`pill ${a.severity === "hard" ? "pill-bad" : "pill-warn"}`}>{a.kind}</span> {a.message}</li>))}</ul>}
              {latest.workpaper.outstanding.length > 0 && <div className="faint" style={{ fontSize: 13 }}>
                In transit: {latest.workpaper.outstanding.map((o) => `${o.reference || o.date} ${formatAmount(o.amount)}`).join(" · ")}</div>}
              {(latest.workpaper.outstanding.length > 0 || latest.workpaper.unrecorded.length > 0) && !latest.approved_by && (
                <ReconciliationExplain key={latest.id} accountId={account.id} reconciliationId={latest.id} />)}
              <table className="table">
                <thead><tr><th>Client ledger</th><th style={{ textAlign: "right" }}>Balance</th><th style={{ textAlign: "right" }}>Last movement</th></tr></thead>
                <tbody>{latest.workpaper.ledgers.map((l) => (
                  <tr key={l.matter}><td>{l.matter}</td><td className="num" style={{ textAlign: "right" }}>{formatAmount(l.balance)}</td>
                    <td className="muted num" style={{ textAlign: "right" }}>{formatDate(l.last_movement)}</td></tr>))}</tbody>
              </table>
              <div className="practice-row space">
                <span className="faint" style={{ fontSize: 12 }}>Prepared by {latest.prepared_by} · {latest.workpaper.matched} bank lines matched</span>
                {latest.approved_by ? <span className="pill pill-ok">Approved by {latest.approved_by}</span> : (
                  <button className="btn btn-sm" disabled={busy || !latest.balanced}
                    onClick={() => void act(async () => { await practiceApi.approveReconciliation(account.id, latest.id); })}>Approve</button>
                )}
              </div>
            </section>
          )}

          <div className="card">
            {entries.length === 0 ? <div className="empty">No cashbook lines yet.</div> : (
              <table className="table">
                <thead><tr><th>Date</th><th>Reference</th><th>Description</th><th>Matter</th><th style={{ textAlign: "right" }}>Amount</th></tr></thead>
                <tbody>{entries.map((e) => (
                  <tr key={e.id}><td className="num">{formatDate(e.date)}</td><td>{e.reference}</td><td className="muted">{e.description}</td>
                    <td>{e.matter ?? <span className="pill pill-bad">Unallocated</span>}</td>
                    <td className="num" style={{ textAlign: "right" }}>{formatAmount(e.amount)}</td></tr>))}</tbody>
              </table>
            )}
          </div>
        </>
      )}
    </main>
  );
}
