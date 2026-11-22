"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api";
import { formatAmount, formatDate } from "@/lib/format";
import {
  COVENANT_FIELD,
  COVENANT_LABELS,
  type CovenantKind,
  type CovenantPeriodInput,
  type CovenantResult,
  type CovenantRun,
  type CovenantTestDate,
  type DocumentSummary,
  type FieldName,
  type TrialBalanceSummary,
} from "@/lib/types";

const KINDS: CovenantKind[] = ["leverage", "interest_cover", "min_liquidity"];
const STATUS_PILL = { pass: "pill-ok", warning: "pill-warn", breach: "pill-bad", not_tested: "", undetermined: "pill-warn" } as const;
const STATUS_WORDS: Partial<Record<CovenantResult["status"], string>> = { not_tested: "Not tested", undetermined: "Needs usage" };
const ALERT_CLASS = { breach: "callout-bad", overdue: "callout-bad", warning: "callout-warn", due: "" } as const;

function today(): string {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 10);
}

/** Display only: the server compares exact values, so rounding here can never change a result. */
function shown(kind: CovenantKind, value: string): string {
  return kind === "min_liquidity" ? formatAmount(value) : `${Number(value).toFixed(2)}x`;
}

/**
 * Headroom h is how far the driver (EBITDA, or cash for liquidity) could fall before a breach. When it is
 * negative the covenant is breached, and -h is exactly how much higher the driver needed to be to comply.
 */
export function headroomSentence(result: Pick<CovenantResult, "kind" | "headroom">): string {
  if (result.headroom === null) return "Headroom is not meaningful for this result.";
  const driver = result.kind === "min_liquidity" ? "Cash" : "EBITDA";
  const pct = Math.abs(Number(result.headroom) * 100).toFixed(1);
  return Number(result.headroom) >= 0
    ? `${driver} could fall ${pct}% before a breach.`
    : `${driver} would have needed to be ${pct}% higher to comply.`;
}

interface Props {
  doc: DocumentSummary;
  onTrace: (name: FieldName | null) => void;
}

export function CovenantsPanel({ doc, onTrace }: Props) {
  const [asOf, setAsOf] = useState(today);
  const [run, setRun] = useState<CovenantRun | null>(null);
  const [blocked, setBlocked] = useState<string | null>(null);
  const [tbs, setTbs] = useState<TrialBalanceSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<{ period: string; kind: CovenantKind } | null>(null);

  const load = useCallback(async () => {
    try {
      setTbs(await api.trialBalances(doc.id));
      try {
        setRun(await api.covenants(doc.id, asOf));
        setBlocked(null);
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 409)) throw e;
        setRun(null);
        setBlocked(e.message.replace(/_/g, " "));
      }
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [doc.id, asOf]);

  // Reload when terms are reviewed (the document's fields change) or the as-of date moves.
  const fieldsKey = doc.fields.map((f) => `${f.id}:${f.status}`).join(",");
  useEffect(() => {
    void load();
  }, [load, fieldsKey]);

  const pick = (period: string, kind: CovenantKind) => {
    setSelected({ period, kind });
    onTrace(COVENANT_FIELD[kind]);
  };

  const selectedDate = run?.test_dates.find((t) => t.period_end === selected?.period);
  const selectedResult = selectedDate?.results.find((r) => r.kind === selected?.kind);
  const tbById = new Map(tbs.map((t) => [t.id, t]));
  const kinds = KINDS.filter((k) => run?.test_dates.some((t) => t.results.some((r) => r.kind === k)));

  return (
    <>
      {error && <div className="error-banner">{error}</div>}

      {blocked ? (
        <div className="callout callout-warn">
          <strong>Covenants are not tested until the terms are verified</strong>
          {blocked}. Thresholds and the cure window need a partner&apos;s sign-off on the Terms tab. You can upload
          trial balances meanwhile.
        </div>
      ) : (
        run && (
          <>
            <div className="as-of">
              <label htmlFor="as-of">Deadlines as of</label>
              <input id="as-of" className="input" type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
            </div>
            {run.alerts.length > 0 && (
              <div className="alerts">
                {run.alerts.map((a, i) => (
                  <div key={i} className={`callout ${ALERT_CLASS[a.severity]}`}>
                    <strong>{a.severity === "due" ? "Coming up" : a.severity[0].toUpperCase() + a.severity.slice(1)}</strong>
                    {a.message}
                  </div>
                ))}
              </div>
            )}

            <table className="sched cov">
              <thead>
                <tr>
                  <th>Quarter end</th>
                  {kinds.map((k) => (
                    <th key={k}>{COVENANT_LABELS[k]}</th>
                  ))}
                  <th>Certificate due</th>
                </tr>
              </thead>
              <tbody>
                {run.test_dates.map((t) => (
                  <TestRow key={t.period_end} t={t} kinds={kinds} selected={selected} onPick={pick} />
                ))}
              </tbody>
            </table>

            {(run.terms.springing_usage !== null || run.terms.cure_runs_from === "delivery") && (
              <PeriodInputs doc={doc} run={run} onChanged={load} />
            )}

            {selectedResult && selectedDate ? (
              <Workings
                result={selectedResult}
                testDate={selectedDate}
                tbs={selectedDate.trial_balance_ids.map((id) => tbById.get(id)).filter(Boolean) as TrialBalanceSummary[]}
                onShowClause={() => onTrace(COVENANT_FIELD[selectedResult.kind])}
              />
            ) : (
              <p className="term-note">
                Click a result to see its workings and the clause it is tested against. Amber means within{" "}
                {(Number(run.terms.warning_headroom) * 100).toFixed(0)}% of the limit.
              </p>
            )}
          </>
        )
      )}

      <TrialBalances doc={doc} tbs={tbs} onChanged={load} />
    </>
  );
}

function TestRow({
  t,
  kinds,
  selected,
  onPick,
}: {
  t: CovenantTestDate;
  kinds: CovenantKind[];
  selected: { period: string; kind: CovenantKind } | null;
  onPick: (period: string, kind: CovenantKind) => void;
}) {
  const due = (
    <td className="num" style={{ padding: "5px 6px", whiteSpace: "nowrap" }}>
      {formatDate(t.certificate_due)}
      {t.cure_deadline && <div className="faint">cure by {formatDate(t.cure_deadline)}</div>}
      {t.cure_pending && <div className="faint">cure: needs delivery date</div>}
    </td>
  );
  if (!t.results.length) {
    return (
      <tr>
        <td className="num" style={{ padding: "5px 6px" }}>
          {formatDate(t.period_end)}
        </td>
        <td colSpan={kinds.length} style={{ padding: "5px 6px", textAlign: "left" }}>
          <span className={`pill ${t.status === "overdue" ? "pill-bad" : ""}`}>
            {t.status === "overdue" ? "Data overdue" : "Awaiting data"}
          </span>
        </td>
        {due}
      </tr>
    );
  }
  return (
    <tr>
      <td className="num" style={{ padding: "5px 6px" }}>
        {formatDate(t.period_end)}
      </td>
      {kinds.map((k) => {
        const r = t.results.find((x) => x.kind === k);
        if (!r) return <td key={k} />;
        return (
          <td key={k}>
            <button
              className="cell-btn cov-cell"
              data-status={r.status}
              data-active={selected?.period === t.period_end && selected.kind === k}
              onClick={() => onPick(t.period_end, k)}
              title={`${COVENANT_LABELS[k]}: ${r.status}`}
            >
              <span className={`pill ${STATUS_PILL[r.status]}`}>{STATUS_WORDS[r.status] ?? (r.value === null ? "n/m" : shown(k, r.value))}</span>
            </button>
          </td>
        );
      })}
      {due}
    </tr>
  );
}

function Workings({
  result,
  testDate,
  tbs,
  onShowClause,
}: {
  result: CovenantResult;
  testDate: CovenantTestDate;
  tbs: TrialBalanceSummary[];
  onShowClause: () => void;
}) {
  const limit = result.kind === "leverage" ? "at most" : "at least";
  return (
    <div className="card workings">
      <div className="term-head">
        <span className="term-label">
          {COVENANT_LABELS[result.kind]} at {formatDate(testDate.period_end)}
        </span>
        <span className={`pill ${STATUS_PILL[result.status]}`}>{STATUS_WORDS[result.status] ?? result.status}</span>
      </div>
      <div className="workings-value">
        {STATUS_WORDS[result.status] ?? (result.value === null ? "Not meaningful" : shown(result.kind, result.value))}
        <span className="muted">
          {" "}
          against {limit} {shown(result.kind, result.threshold)}
        </span>
      </div>
      {result.note && <div className="term-note">{result.note}</div>}
      <table className="workings-table">
        <tbody>
          {result.workings.map((w) => (
            <tr key={w.label} data-total={/^(Net debt|EBITDA)/.test(w.label) || undefined}>
              <td>{w.label}</td>
              <td className="num">{formatAmount(w.amount)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="term-meta" style={{ marginTop: 8 }}>
        {!STATUS_WORDS[result.status] && <span>{headroomSentence(result)}</span>}
      </div>
      <div className="chips" style={{ marginTop: 10 }}>
        <button className="chip" onClick={onShowClause}>
          <span className="faint">Threshold from</span> <b>the agreement</b> <span className="faint">(show clause)</span>
        </button>
        {tbs.map((tb) => (
          <span key={tb.id} className="chip" title={`SHA-256 ${tb.sha256}`}>
            <span className="faint">TB</span> <b>{formatDate(tb.period_end)}</b>
            <span className="faint">{tb.lines} lines</span>
          </span>
        ))}
      </div>
    </div>
  );
}

/** The revolver drawn and certificate delivery date of each test date: entered, then confirmed by someone else. */
function PeriodInputs({ doc, run, onChanged }: { doc: DocumentSummary; run: CovenantRun; onChanged: () => void }) {
  const springing = run.terms.springing_usage !== null;
  const delivery = run.terms.cure_runs_from === "delivery";
  const [period, setPeriod] = useState(run.test_dates[0]?.period_end ?? "");
  const [drawn, setDrawn] = useState("");
  const [delivered, setDelivered] = useState("");
  const [basis, setBasis] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      setDrawn("");
      setDelivered("");
      setBasis("");
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const describe = (x: CovenantPeriodInput) =>
    [x.revolver_drawn !== null && `drawn ${formatAmount(x.revolver_drawn)}`,
     x.delivered_on && `delivered ${formatDate(x.delivered_on)}`].filter(Boolean).join(" · ");

  return (
    <div className="term" style={{ cursor: "default", marginTop: 14 }}>
      <div className="term-head">
        <span className="term-label">Test date facts</span>
        <span className="faint" style={{ fontSize: 12 }}>used once a second person confirms</span>
      </div>
      {run.period_inputs.length > 0 && (
        <ul className="chain">
          {run.period_inputs.map((x) => (
            <li key={x.period_end}>
              <span>
                <b className="num">{formatDate(x.period_end)}</b> {describe(x)}{" "}
                <span className="faint">
                  {x.basis} · by {x.entered_by}{x.confirmed_by ? ` · confirmed by ${x.confirmed_by}` : ""}
                </span>
              </span>
              {!x.confirmed_by && (
                <button className="btn btn-sm" disabled={busy}
                  onClick={() => void act(() => api.confirmPeriodInput(doc.id, x.period_end))}>
                  Confirm
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      <form className="term-actions" onSubmit={(e) => {
        e.preventDefault();
        void act(() => api.savePeriodInput(doc.id, period, {
          revolver_drawn: drawn.trim() || null, delivered_on: delivered || null, basis,
        }));
      }}>
        <select className="input" value={period} onChange={(e) => setPeriod(e.target.value)} aria-label="Test date">
          {run.test_dates.map((t) => <option key={t.period_end} value={t.period_end}>{formatDate(t.period_end)}</option>)}
        </select>
        {springing && <input className="input" value={drawn} onChange={(e) => setDrawn(e.target.value)}
          placeholder="Revolver drawn" aria-label="Revolver drawn" />}
        {delivery && <input className="input" type="date" value={delivered} onChange={(e) => setDelivered(e.target.value)}
          aria-label="Certificate delivered on" />}
        <input className="input" value={basis} onChange={(e) => setBasis(e.target.value)}
          placeholder="Basis, e.g. agent's statement" aria-label="Basis" />
        <button className="btn btn-sm" disabled={busy || basis.trim().length < 10 || (!drawn.trim() && !delivered)}>Save</button>
      </form>
      {error && <div className="error-banner">{error}</div>}
    </div>
  );
}

function TrialBalances({
  doc,
  tbs,
  onChanged,
}: {
  doc: DocumentSummary;
  tbs: TrialBalanceSummary[];
  onChanged: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [periodEnd, setPeriodEnd] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <div className="term" style={{ cursor: "default", marginTop: 14 }}>
      <div className="term-head">
        <span className="term-label">Trial balances ({doc.filename})</span>
        <span className="faint" style={{ fontSize: 12 }}>
          {tbs.length || "None"}
        </span>
      </div>
      {tbs.length > 0 && (
        <ul className="chain">
          {tbs.map((tb) => (
            <li key={tb.id}>
              <span>
                <b className="num">{formatDate(tb.period_end)}</b>{" "}
                <span className="faint">
                  {tb.filename} · {tb.lines} lines · by {tb.uploaded_by}
                </span>
              </span>
              <button
                className="btn btn-sm btn-ghost btn-danger"
                disabled={busy}
                aria-label={`Delete the ${tb.period_end} trial balance`}
                onClick={() => void act(() => api.deleteTrialBalance(tb.id))}
              >
                Delete
              </button>
            </li>
          ))}
        </ul>
      )}
      <form
        className="term-actions"
        onSubmit={(e) => {
          e.preventDefault();
          const file = input.current?.files?.[0];
          if (!file || !periodEnd) {
            setError("Choose the quarter-end date and a CSV file.");
            return;
          }
          void act(() => api.uploadTrialBalance(doc.id, file, periodEnd));
        }}
      >
        <input
          className="input"
          type="date"
          value={periodEnd}
          onChange={(e) => setPeriodEnd(e.target.value)}
          aria-label="Quarter-end date"
        />
        <input ref={input} type="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" aria-label="Trial balance CSV" />
        <button className="btn btn-sm" disabled={busy}>
          {busy ? "Uploading…" : "Upload trial balance"}
        </button>
      </form>
      <div className="term-note">
        CSV or Excel with <span className="mono">code, name, category, balance</span> (debit positive) or{" "}
        <span className="mono">debit, credit</span>. Categories: cash, borrowings, lease_liabilities, revenue,
        operating_expenses, depreciation_amortisation, interest_expense, other. It must balance to the cent. Each
        quarter holds that quarter&apos;s P&amp;L only.
      </div>
      {error && <div className="term-error">{error}</div>}
    </div>
  );
}
