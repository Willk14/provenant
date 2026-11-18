"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "@/lib/api";
import { formatAmount } from "@/lib/format";
import { taxApi, type Computation } from "@/lib/tax";
import type { DocumentSummary } from "@/lib/types";

interface Props {
  clientId: string;
  yearEnd: string;
  onOpenMapping: () => void;
  onOpenRecords: () => void;
}

function pct(rate: string): string {
  return `${(Number(rate) * 100).toFixed(0)}%`;
}

export function TaxTab({ clientId, yearEnd, onOpenMapping, onOpenRecords }: Props) {
  const [computation, setComputation] = useState<Computation | null>(null);
  const [blocked, setBlocked] = useState<string | null>(null);
  const [leases, setLeases] = useState<DocumentSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  const recompute = useCallback(async () => {
    try {
      setComputation(await taxApi.computation(clientId, yearEnd));
      setBlocked(null);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 409)) throw e;
      setComputation(null);
      setBlocked(e.message);
    }
  }, [clientId, yearEnd]);

  const load = useCallback(async () => {
    try {
      const docs = await api.documents();
      setLeases(docs.filter((d) => d.kind === "lease"));
      await recompute();
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [clientId, yearEnd, recompute]);

  useEffect(() => {
    void load();
  }, [load]);

  async function link(documentId: string, client: string | null) {
    try {
      await taxApi.linkLease(documentId, client);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const linked = leases.filter((d) => d.client_id === clientId);
  const unlinked = leases.filter((d) => !d.client_id);

  return (
    <div className="tax-layout">
      <section>
        {error && <div className="error-banner">{error}</div>}
        {blocked ? (
          <div className="callout callout-warn">
            <strong>
              {blocked.startsWith("lease ")
                ? "The computation is waiting on a linked lease"
                : "The computation is waiting on the mapping"}
            </strong>
            {blocked}.{" "}
            {blocked.startsWith("lease ") ? (
              <span className="muted">Verify the lease&apos;s terms (and its amendments) so its schedule can run.</span>
            ) : (
              <button className="btn btn-sm" onClick={onOpenMapping} style={{ marginLeft: 6 }}>
                Open financial mapping
              </button>
            )}
          </div>
        ) : computation ? (
          <ComputationView c={computation} clientId={clientId} />
        ) : (
          <div className="empty">Computing…</div>
        )}
      </section>

      <aside>
        <div className="term" style={{ cursor: "default" }}>
          <div className="term-label">Preparer inputs</div>
          <p className="term-note" style={{ marginTop: 2 }}>
            Prior-year position, capital deduction classes, the fixed asset register, tax credits and supporting
            documents live under Records &amp; inputs.
          </p>
          <div className="term-actions">
            <button className="btn btn-sm" type="button" onClick={onOpenRecords}>Open records &amp; inputs</button>
          </div>
        </div>

        <div className="term" style={{ cursor: "default" }}>
          <div className="term-label">Leases (IFRS 16 tax adjustments)</div>
          <p className="term-note" style={{ marginTop: 2 }}>
            Linked leases feed the year&apos;s rentals, depreciation and interest from their verified schedules.
          </p>
          <ul className="chain">
            {linked.map((d) => (
              <li key={d.id}>
                <Link className="link" href={`/documents/${d.id}?tab=schedule`}>{d.filename}</Link>
                <button className="btn btn-sm btn-ghost" onClick={() => void link(d.id, null)}>Unlink</button>
              </li>
            ))}
          </ul>
          {unlinked.length > 0 && (
            <select className="input" value="" aria-label="Link a lease" style={{ marginTop: 8, width: "100%" }}
              onChange={(e) => e.target.value && void link(e.target.value, clientId)}>
              <option value="">Link a lease…</option>
              {unlinked.map((d) => (
                <option key={d.id} value={d.id}>{d.filename}</option>
              ))}
            </select>
          )}
        </div>
      </aside>
    </div>
  );
}

function Row({ label, amount, strong, note }: { label: string; amount: string; strong?: boolean; note?: string }) {
  return (
    <tr data-total={strong || undefined}>
      <td>
        {label}
        {note && <div className="faint" style={{ fontSize: 12 }}>{note}</div>}
      </td>
      <td className="num">{formatAmount(amount)}</td>
    </tr>
  );
}

function ComputationView({ c, clientId }: { c: Computation; clientId: string }) {
  const due = Number(c.tax_due);
  return (
    <div className="card comp">
      <div className="comp-head">
        <div>
          <div className="term-label">Corporation tax computation</div>
          <div className="faint" style={{ fontSize: 12 }}>Rules {c.rules_version} · amounts in the trial balance currency</div>
        </div>
        <div className="comp-total">
          <div className="faint" style={{ fontSize: 12 }}>
            {c.credits.length ? (due < 0 ? "Tax refundable" : "Tax due after credits") : `Tax payable at ${pct(c.rate)}`}
          </div>
          <div className="workings-value" style={{ margin: 0 }}>
            {formatAmount((c.credits.length ? c.tax_due : c.tax_payable).replace(/^-/, ""))}
          </div>
        </div>
      </div>
      {c.warnings.map((w) => (
        <div key={w} className="callout callout-warn" style={{ padding: "6px 10px", fontSize: 13 }}>{w}</div>
      ))}
      <table className="workings-table comp-table">
        <tbody>
          <Row label="Profit before tax" amount={c.profit_before_tax} strong />
          {c.adjustments.map((a) => (
            <tr key={a.label}>
              <td>
                {a.label}
                <div className="faint" style={{ fontSize: 12 }}>{a.basis}</div>
                <div className="chips" style={{ marginTop: 4 }}>
                  {a.sources.map((s) =>
                    c.accounts[s] ? (
                      <span key={s} className="chip"><b>{c.accounts[s]}</b></span>
                    ) : c.leases.some((l) => l.document_id === s) ? (
                      <Link key={s} className="chip chip-elsewhere" href={`/documents/${s}?tab=schedule`}>
                        <b>{c.leases.find((l) => l.document_id === s)?.name}</b> <span aria-hidden>↗</span>
                      </Link>
                    ) : (
                      <span key={s} className="chip">
                        <span className="faint">
                          {c.allowances.find((x) => `allowance:${x.name}` === s)?.from_register ? "register" : "input"}
                        </span>{" "}
                        <b>{s.replace(/^allowance:/, "")}</b>
                      </span>
                    ),
                  )}
                </div>
              </td>
              <td className="num">{formatAmount(a.amount)}</td>
            </tr>
          ))}
          <Row label="Taxable profit before losses" amount={c.taxable_before_losses} strong />
          <Row
            label="Tax losses brought forward used"
            amount={Number(c.losses_used) ? `-${c.losses_used}` : "0"}
            note={`of ${formatAmount(c.losses_brought_forward)} available`}
          />
          <Row label="Taxable profit" amount={c.taxable_profit} strong />
          <Row label={`Tax payable at ${pct(c.rate)}`} amount={c.tax_payable} strong />
          {c.credits.map((line) => (
            <tr key={line.label}>
              <td>
                Less: {line.label.toLowerCase()}
                <div className="faint" style={{ fontSize: 12 }}>{line.basis}</div>
                <div className="chips" style={{ marginTop: 4 }}>
                  {line.sources.map((s) => {
                    const item = c.credit_items[Number(s.replace("credit:", ""))];
                    if (!item) return null;
                    const text = `${item.description}${item.reference ? ` · ${item.reference}` : ""} · ${formatAmount(item.amount)}`;
                    return item.evidence ? (
                      <button key={s} type="button" className="chip chip-elsewhere"
                        onClick={() => void taxApi.openAttachment(clientId, item.evidence as string, item.evidence_filename ?? "")}>
                        <b>{text}</b> <span className="faint">{item.evidence_filename}</span> <span aria-hidden>↗</span>
                      </button>
                    ) : (
                      <span key={s} className="chip"><b>{text}</b> <span className="faint">no evidence attached</span></span>
                    );
                  })}
                </div>
              </td>
              <td className="num">{formatAmount(line.amount)}</td>
            </tr>
          ))}
          {c.credits.length > 0 && (
            <Row label={due < 0 ? "Tax refundable" : "Tax due"} amount={c.tax_due.replace(/^-/, "")} strong />
          )}
          {c.losses_carried_forward !== "0" && Number(c.losses_carried_forward) > 0 && (
            <Row label="Tax losses carried forward" amount={c.losses_carried_forward} />
          )}
        </tbody>
      </table>

      <div className="term-label" style={{ marginTop: 16 }}>Deferred tax (IAS 12)</div>
      <table className="workings-table comp-table">
        <tbody>
          {c.deferred.map((d) => (
            <Row key={d.label} label={d.label} amount={d.amount} note={d.basis} />
          ))}
          <Row
            label={Number(c.deferred_tax) >= 0 ? `Deferred tax liability at ${pct(c.rate)}` : `Deferred tax asset at ${pct(c.rate)}`}
            amount={c.deferred_tax.replace(/^-/, "")}
            strong
          />
        </tbody>
      </table>
      <p className="term-note">
        Working papers for a tax professional, not tax advice. Rates, deductions and treatments must be confirmed
        against the law in force for the year.
      </p>
    </div>
  );
}
