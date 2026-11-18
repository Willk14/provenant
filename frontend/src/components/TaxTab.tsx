"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "@/lib/api";
import { formatAmount } from "@/lib/format";
import { taxApi, type Allowance, type Computation, type Inputs } from "@/lib/tax";
import type { DocumentSummary } from "@/lib/types";

interface Props {
  clientId: string;
  yearEnd: string;
  onOpenMapping: () => void;
}

const EMPTY_CLASS: Allowance = {
  name: "",
  wdv_brought_forward: "0",
  additions: "0",
  rate_percent: "",
  method: "reducing",
  basis: "",
};

function pct(rate: string): string {
  return `${(Number(rate) * 100).toFixed(0)}%`;
}

export function TaxTab({ clientId, yearEnd, onOpenMapping }: Props) {
  const [computation, setComputation] = useState<Computation | null>(null);
  const [blocked, setBlocked] = useState<string | null>(null);
  const [inputs, setInputs] = useState<Inputs | null>(null);
  const [leases, setLeases] = useState<DocumentSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

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
      const [loaded, docs] = await Promise.all([taxApi.inputs(clientId, yearEnd), api.documents()]);
      setInputs(loaded);
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

  async function save() {
    if (!inputs) return;
    setSaving(true);
    setError(null);
    try {
      await taxApi.saveInputs(clientId, yearEnd, inputs);
      await recompute();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

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
  const update = (i: number, patch: Partial<Allowance>) =>
    setInputs((cur) => cur && { ...cur, allowances: cur.allowances.map((a, j) => (j === i ? { ...a, ...patch } : a)) });

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
          <ComputationView c={computation} />
        ) : (
          <div className="empty">Computing…</div>
        )}
      </section>

      <aside>
        <div className="term" style={{ cursor: "default" }}>
          <div className="term-label">Preparer inputs</div>
          {inputs && (
            <>
              <label className="field">
                <span>Tax losses brought forward</span>
                <input
                  className="input"
                  value={inputs.losses_brought_forward}
                  onChange={(e) => setInputs({ ...inputs, losses_brought_forward: e.target.value })}
                />
              </label>
              <div className="term-label" style={{ marginTop: 12 }}>
                Capital deductions (Second Schedule)
              </div>
              <p className="term-note" style={{ marginTop: 2 }}>
                Rates change with Finance Acts, so each class carries the rate you apply and its basis.
              </p>
              {inputs.allowances.map((a, i) => (
                <div key={i} className="allowance">
                  <input className="input" placeholder="Class, e.g. Plant and machinery" value={a.name}
                    onChange={(e) => update(i, { name: e.target.value })} aria-label="Class name" />
                  <div className="allowance-grid">
                    <label className="field"><span>WDV b/f</span>
                      <input className="input" value={a.wdv_brought_forward} onChange={(e) => update(i, { wdv_brought_forward: e.target.value })} /></label>
                    <label className="field"><span>Additions</span>
                      <input className="input" value={a.additions} onChange={(e) => update(i, { additions: e.target.value })} /></label>
                    <label className="field"><span>Rate %</span>
                      <input className="input" value={a.rate_percent} onChange={(e) => update(i, { rate_percent: e.target.value })} /></label>
                    <label className="field"><span>Method</span>
                      <select className="input" value={a.method} onChange={(e) => update(i, { method: e.target.value as Allowance["method"] })}>
                        <option value="reducing">Reducing balance</option>
                        <option value="straight_line">Straight line</option>
                      </select></label>
                  </div>
                  <input className="input" placeholder="Basis, e.g. Second Schedule para. … (Finance Act year)" value={a.basis}
                    onChange={(e) => update(i, { basis: e.target.value })} aria-label="Basis for the rate" />
                  <button className="btn btn-sm btn-ghost btn-danger" type="button"
                    onClick={() => setInputs({ ...inputs, allowances: inputs.allowances.filter((_, j) => j !== i) })}>
                    Remove class
                  </button>
                </div>
              ))}
              <div className="term-actions">
                <button className="btn btn-sm" type="button"
                  onClick={() => setInputs({ ...inputs, allowances: [...inputs.allowances, { ...EMPTY_CLASS }] })}>
                  Add class
                </button>
                <button className="btn btn-primary btn-sm" disabled={saving} onClick={save}>
                  {saving ? "Saving…" : "Save and recompute"}
                </button>
              </div>
            </>
          )}
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

function ComputationView({ c }: { c: Computation }) {
  return (
    <div className="card comp">
      <div className="comp-head">
        <div>
          <div className="term-label">Corporation tax computation</div>
          <div className="faint" style={{ fontSize: 12 }}>Rules {c.rules_version} · amounts in the trial balance currency</div>
        </div>
        <div className="comp-total">
          <div className="faint" style={{ fontSize: 12 }}>Tax payable at {pct(c.rate)}</div>
          <div className="workings-value" style={{ margin: 0 }}>{formatAmount(c.tax_payable)}</div>
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
                      <span key={s} className="chip"><span className="faint">input</span> <b>{s.replace(/^allowance:/, "")}</b></span>
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
