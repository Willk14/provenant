"use client";

import { displayValue } from "@/components/TermsPanel";
import { formatAmount, formatDate } from "@/lib/format";
import { FIGURE_LABELS, TRACE, type Figure } from "@/lib/trace";
import { FIELD_LABELS, type Field, type FieldName, type Period, type Schedule } from "@/lib/types";

export interface Selection {
  figure: Figure;
  period: number | null; // null = a summary figure
}

interface Props {
  schedule: Schedule | null;
  blocked: string | null;
  selection: Selection | null;
  onSelectFigure: (selection: Selection) => void;
  onFocusField: (name: FieldName) => void;
  onDownload: () => void;
}

const COLUMNS: { figure: Figure; label: string; value: (p: Period) => string }[] = [
  { figure: "end_date", label: "Period end", value: (p) => formatDate(p.end_date) },
  { figure: "opening", label: "Opening", value: (p) => formatAmount(p.opening) },
  { figure: "interest", label: "Interest", value: (p) => formatAmount(p.interest) },
  { figure: "payment", label: "Payment", value: (p) => formatAmount(p.payment) },
  { figure: "closing", label: "Closing", value: (p) => formatAmount(p.closing) },
  { figure: "depreciation", label: "Depreciation", value: (p) => formatAmount(p.depreciation) },
];

function toCents(value: string): bigint {
  const negative = value.startsWith("-");
  const [whole, fraction = ""] = value.replace(/^-/, "").split(".");
  const cents = BigInt(whole || "0") * 100n + BigInt(fraction.padEnd(2, "0").slice(0, 2));
  return negative ? -cents : cents;
}

function sum(periods: Period[], key: "interest" | "depreciation"): string {
  // Sum in integer cents so the total is exact (amounts are already rounded to cents by the engine).
  const cents = periods.reduce((acc, p) => acc + toCents(p[key]), 0n);
  const sign = cents < 0n ? "-" : "";
  const abs = (cents < 0n ? -cents : cents).toString().padStart(3, "0");
  return `${sign}${abs.slice(0, -2)}.${abs.slice(-2)}`;
}

export function SchedulePanel({ schedule, blocked, selection, onSelectFigure, onFocusField, onDownload }: Props) {
  if (blocked) {
    return (
      <div className="callout callout-warn">
        <strong>The schedule is waiting on the contract terms</strong>
        {blocked}. The calculation only runs on terms that are verified, so approve, correct or mark the
        terms on the Terms tab first.
      </div>
    );
  }
  if (!schedule) return <div className="empty">Calculating…</div>;

  const inputs = new Map<FieldName, Field>(schedule.inputs.map((f) => [f.name, f]));
  const traced = selection ? TRACE[selection.figure] : [];
  const is = (figure: Figure, period: number | null) =>
    selection?.figure === figure && selection.period === period;

  const summary: { figure: Figure; label: string; value: string }[] = [
    { figure: "liability", label: "Lease liability", value: schedule.initial_liability },
    { figure: "rou", label: "Right-of-use asset", value: schedule.initial_rou_asset },
    { figure: "interest", label: "Total interest", value: sum(schedule.periods, "interest") },
    { figure: "depreciation", label: "Total depreciation", value: sum(schedule.periods, "depreciation") },
  ];

  return (
    <>
      {selection ? (
        <div className="trace">
          <div className="trace-title">
            {FIGURE_LABELS[selection.figure]}
            {selection.period !== null && `, period ${selection.period}`} comes from
          </div>
          <div className="chips">
            {traced.map((name) => {
              const f = inputs.get(name);
              return (
                <button key={name} className="chip" onClick={() => onFocusField(name)}>
                  <span className="faint">{FIELD_LABELS[name]}</span>
                  <b>{f ? displayValue(f) : "-"}</b>
                  {f && <span className="faint">p.{f.page}</span>}
                </button>
              );
            })}
          </div>
        </div>
      ) : (
        <p className="muted" style={{ marginTop: 0, fontSize: 13 }}>
          Click any figure to see which contract terms produced it, highlighted on the PDF.
        </p>
      )}

      <div className="figures">
        {summary.map((s) => (
          <button
            key={s.label}
            className="figure"
            data-active={is(s.figure, null)}
            onClick={() => onSelectFigure({ figure: s.figure, period: null })}
          >
            <div className="k">{s.label}</div>
            <div className="v">{formatAmount(s.value)}</div>
          </button>
        ))}
      </div>

      <table className="sched">
        <thead>
          <tr>
            <th>#</th>
            {COLUMNS.map((c) => (
              <th key={c.figure}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {schedule.periods.map((p) => (
            <tr key={p.number}>
              <td className="faint num" style={{ padding: "0 6px" }}>
                {p.number}
              </td>
              {COLUMNS.map((c) => (
                <td key={c.figure}>
                  <button
                    className="cell-btn"
                    data-active={is(c.figure, p.number)}
                    onClick={() => onSelectFigure({ figure: c.figure, period: p.number })}
                  >
                    {c.value(p)}
                  </button>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>

      <div style={{ marginTop: 12 }}>
        <button className="btn" onClick={onDownload}>
          Download Excel (schedule, journal, provenance)
        </button>
      </div>
    </>
  );
}
