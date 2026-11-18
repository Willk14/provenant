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

  async function approve() {
    try {
      await taxApi.approveComputation(clientId, yearEnd);
      await recompute();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function exportWorkbook() {
    try {
      const data = await taxApi.exportComputation(clientId, yearEnd);
      const url = URL.createObjectURL(new Blob([data], {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      }));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `tax-computation-${yearEnd}.xlsx`;
      anchor.click();
      URL.revokeObjectURL(url);
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
                : blocked.includes("opening tax residual values")
                  ? "The computation needs asset opening values"
                  : blocked.includes("fixed asset register")
                    ? "The computation needs per-asset disposal data"
                : "The computation is waiting on the mapping"}
            </strong>
            {blocked}.{" "}
            {blocked.startsWith("lease ") ? (
              <span className="muted">Verify the lease&apos;s terms (and its amendments) so its schedule can run.</span>
            ) : blocked.includes("opening tax residual values") || blocked.includes("fixed asset register") ? (
              <button className="btn btn-sm" onClick={onOpenRecords} style={{ marginLeft: 6 }}>Open records &amp; inputs</button>
            ) : (
              <button className="btn btn-sm" onClick={onOpenMapping} style={{ marginLeft: 6 }}>
                Open financial mapping
              </button>
            )}
          </div>
        ) : computation ? (
          <ComputationView c={computation} clientId={clientId} onApprove={approve} onExport={exportWorkbook} />
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

function ComputationView({ c, clientId, onApprove, onExport }: {
  c: Computation; clientId: string; onApprove: () => void; onExport: () => void;
}) {
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
      <div className={c.changed_since_approval ? "callout callout-warn" : "callout"} style={{ marginTop: 12 }}>
        {c.approval ? (
          <span>Approved by {c.approval.reviewer} on {new Date(c.approval.approved_at).toLocaleString()}.
            {c.changed_since_approval ? " Changed since approval." : " Current computation matches this approval."}</span>
        ) : <span>This computation has not been approved.</span>}{" "}
        <button type="button" className="btn btn-sm" onClick={onApprove}>Approve computation</button>
        <button type="button" className="btn btn-sm" onClick={onExport}>Export Excel</button>
      </div>
      {c.warnings.map((w) => (
        <div key={w} className="callout callout-warn" style={{ padding: "6px 10px", fontSize: 13 }}>{w}</div>
      ))}
      {c.instalment_plan && (
        <>
          <h3 style={{ margin: "18px 0 6px" }}>Instalment tax plan</h3>
          <p className="term-note">
            Provisional schedule under Income Tax Act s.12 and the Twelfth Schedule. Basis: {c.instalment_plan.basis_used};
            current-year estimate {formatAmount(c.instalment_plan.current_year_basis)} after withholding of {formatAmount(c.instalment_plan.withholding_reduction)}.
            {c.instalment_plan.prior_year_basis !== null && ` 110% of prior-year assessment before withholding: ${formatAmount(c.instalment_plan.prior_year_basis)}.`}
            {` Agriculture income ratio: ${(Number(c.instalment_plan.agriculture_income_ratio) * 100).toFixed(1)}%.`}
            This is a planning estimate; reconcile with the filed assessment and actual payment receipts.
          </p>
          <table className="table register-table">
            <thead><tr><th>Instalment</th><th>Due date</th><th className="num">Proportion</th>
              <th className="num">Planned</th><th className="num">Paid recorded</th><th className="num">Outstanding</th></tr></thead>
            <tbody>{c.instalment_plan.installments.map((item) => (
              <tr key={item.number}><td>{item.number}<div className="faint">{item.basis}</div>
                <div className="faint">Sources: {item.sources.join(", ")}</div></td>
                <td>{new Date(`${item.due_date}T00:00:00Z`).toLocaleDateString()}</td>
                <td className="num">{(Number(item.proportion) * 100).toFixed(0)}%</td>
                <td className="num">{formatAmount(item.amount)}</td>
                <td className="num">{formatAmount(item.paid_to_date)}</td>
                <td className="num">{formatAmount(item.outstanding)}</td></tr>
            ))}</tbody>
          </table>
          {Number(c.instalment_plan.unallocated_instalment_payments) > 0 && (
            <div className="callout callout-warn" style={{ marginTop: 8 }}>
              {formatAmount(c.instalment_plan.unallocated_instalment_payments)} of recorded instalment credits have no payment date and are not allocated to due dates. Add dates under Records &amp; inputs.
            </div>
          )}
          <p className="term-note">Balance of tax deadline: {new Date(`${c.instalment_plan.balance_due_date}T00:00:00Z`).toLocaleDateString()} · {c.instalment_plan.balance_due_basis}. Current computation balance: {formatAmount(c.instalment_plan.balance_due_amount.replace(/^-/, ""))}{Number(c.instalment_plan.balance_due_amount) < 0 ? " refundable" : " due"}.</p>
        </>
      )}
      {c.allowances.some((a) => a.asset_schedule.length > 0) && (
        <>
          <h3 style={{ margin: "18px 0 6px" }}>Asset tax schedule</h3>
          <p className="term-note">Per-asset opening values are preparer inputs; register cost and disposal proceeds are linked by asset code.</p>
          <table className="table register-table">
            <thead><tr><th>Asset</th><th className="num">Opening value</th><th className="num">Addition</th>
              <th className="num">Allowance</th><th className="num">Proceeds</th><th className="num">Disposal / cessation result</th>
              <th className="num">Closing value</th></tr></thead>
            <tbody>{c.allowances.flatMap((a) => a.asset_schedule.map((item) => (
              <tr key={item.code}><td><span className="mono">{item.code}</span><div className="faint">{a.name} · {item.source}</div>
                <div className="faint">Rate basis: {item.rate_basis}</div>
                {a.business_ceased && <div className="faint">Cessation balancing result; included in the reviewed tax computation.</div>}
                {item.disposal_basis && <div className="faint">Disposal basis: {item.disposal_basis}</div>}</td>
                <td className="num">{formatAmount(item.opening_wdv)}</td><td className="num">{formatAmount(item.additions)}</td>
                <td className="num">{formatAmount(item.allowance)}</td><td className="num">{formatAmount(item.proceeds)}</td>
                <td className="num">{item.disposal_result === null ? "—" : formatAmount(item.disposal_result)}</td>
                <td className="num">{formatAmount(item.closing_wdv)}</td></tr>
            )))}</tbody>
          </table>
        </>
      )}
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
                          {s.startsWith("asset:") ? "fixed asset" : (c.allowances.find((x) => `allowance:${x.name}` === s)?.from_register ? "register" : "input")}
                        </span>{" "}
                        <b>{s.replace(/^(allowance|asset):/, "")}</b>
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
