"use client";

import { useState } from "react";

import { formatAmount } from "@/lib/format";
import { claudeApi, type Explanation } from "@/lib/practice2";

/** Claude drafts the letter asking the client for the missing KYC items. It is never sent from here (ADR 0020). */
export function KycRequest({ matterId }: { matterId: string }) {
  const [letter, setLetter] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  return (
    <div className="claude-assist">
      <div className="practice-row">
        <button type="button" className="btn btn-sm" disabled={busy} onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            setLetter((await claudeApi.kycRequest(matterId)).letter);
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}>{busy ? "Claude is drafting…" : "Draft the request with Claude"}</button>
        <span className="faint" style={{ fontSize: 12 }}>A draft for you to edit and send yourself.</span>
      </div>
      {error && <div className="error-banner">{error}</div>}
      {letter !== null && (
        <>
          <textarea className="input" rows={10} value={letter} onChange={(e) => setLetter(e.target.value)} aria-label="Request letter" />
          <div><button type="button" className="btn btn-sm btn-ghost" onClick={async () => {
            await navigator.clipboard.writeText(letter);
            setCopied(true);
          }}>{copied ? "Copied" : "Copy letter"}</button></div>
        </>
      )}
    </div>
  );
}

/** Claude's likely causes for what did not reconcile. The differences are worked out by Provenant, not by Claude. */
export function ReconciliationExplain({ accountId, reconciliationId }: { accountId: string; reconciliationId: string }) {
  const [items, setItems] = useState<Explanation[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="claude-assist">
      <div className="practice-row">
        <button type="button" className="btn btn-sm" disabled={busy} onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            setItems((await claudeApi.explainReconciliation(accountId, reconciliationId)).explanations);
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}>{busy ? "Claude is reading…" : "Explain the breaks with Claude"}</button>
        <span className="faint" style={{ fontSize: 12 }}>Suggestions only: fix breaks with cashbook entries, then prepare again.</span>
      </div>
      {error && <div className="error-banner">{error}</div>}
      {items && (items.length === 0 ? <div className="faint" style={{ fontSize: 13 }}>Claude found nothing to group.</div> : (
        <ul className="practice-list">{items.map((x, i) => (
          <li key={i}>
            <b>{x.likely_cause}</b> {x.next_step}
            <div className="faint" style={{ fontSize: 12 }}>
              {[...x.entries.map((e) => `cashbook ${e.reference || e.date} ${formatAmount(e.amount)}`),
                ...x.bank_lines.map((b) => `bank ${b.reference || b.date} ${formatAmount(b.amount)}`)].join(" · ")}
              {" "}· difference {formatAmount(x.difference)}
            </div>
          </li>
        ))}</ul>
      ))}
    </div>
  );
}
