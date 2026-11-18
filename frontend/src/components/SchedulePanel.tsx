"use client";

import Link from "next/link";
import { Fragment } from "react";

import { displayValue } from "@/components/TermsPanel";
import { formatAmount, formatDate, shortHash } from "@/lib/format";
import { FIGURE_LABELS, resolveInputs, type Figure, type Selection } from "@/lib/trace";
import { fieldLabel, type FieldName, type Period, type Schedule, type SeparateLease } from "@/lib/types";

interface Props {
  documentId: string;
  schedule: Schedule | null;
  blocked: string | null;
  selection: Selection | null;
  filenames: ReadonlyMap<string, string>;
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

function fromCents(cents: bigint): string {
  const sign = cents < 0n ? "-" : "";
  const abs = (cents < 0n ? -cents : cents).toString().padStart(3, "0");
  return `${sign}${abs.slice(0, -2)}.${abs.slice(-2)}`;
}

/** Sum in integer cents so totals are exact (the engine has already rounded each amount). */
function sum(values: string[]): string {
  return fromCents(values.reduce((acc, v) => acc + toCents(v), 0n));
}

function signed(value: string): string {
  return toCents(value) > 0n ? `+${formatAmount(value)}` : formatAmount(value);
}

export function SchedulePanel({
  documentId,
  schedule,
  blocked,
  selection,
  filenames,
  onSelectFigure,
  onFocusField,
  onDownload,
}: Props) {
  if (blocked) {
    return (
      <div className="callout callout-warn">
        <strong>The schedule is waiting on the contract terms</strong>
        {blocked}. The calculation only runs on verified terms, from the lease and every amendment, so approve,
        correct or mark them first.
      </div>
    );
  }
  if (!schedule) return <div className="empty">Calculating…</div>;

  const traced = selection ? resolveInputs(schedule, selection) : [];
  const is = (figure: Figure, period: number | null) =>
    selection?.figure === figure && selection.period === period;
  const remeasuredAfter = new Map(schedule.remeasurements.map((r, i) => [r.after_period, { r, segment: i + 1 }]));
  const gains = schedule.remeasurements.map((r) => r.gain);

  const summary: { figure: Figure; label: string; value: string }[] = [
    { figure: "liability", label: "Lease liability at commencement", value: schedule.initial_liability },
    { figure: "rou", label: "Right-of-use asset at commencement", value: schedule.initial_rou_asset },
    { figure: "interest", label: "Total interest", value: sum(schedule.periods.map((p) => p.interest)) },
    { figure: "depreciation", label: "Total depreciation", value: sum(schedule.periods.map((p) => p.depreciation)) },
  ];

  return (
    <>
      {selection ? (
        <div className="trace">
          <div className="trace-title">
            {FIGURE_LABELS[selection.figure]}
            {selection.period !== null && selection.figure !== "remeasurement" && `, period ${selection.period}`}
            {selection.figure === "remeasurement" && ` after period ${selection.period}`} comes from
          </div>
          <div className="chips">
            {traced.map((f) => {
              const elsewhere = f.document_id !== documentId;
              const content = (
                <>
                  <span className="faint">{fieldLabel(f.name, elsewhere ? "amendment" : "lease")}</span>
                  <b>{displayValue(f)}</b>
                  <span className="faint">
                    {elsewhere ? `${filenames.get(f.document_id) ?? "other document"}, ` : ""}p.{f.page}
                  </span>
                </>
              );
              return elsewhere ? (
                <Link key={f.id} className="chip chip-elsewhere" href={`/documents/${f.document_id}?field=${f.name}`}>
                  {content} <span aria-hidden>↗</span>
                </Link>
              ) : (
                <button key={f.id} className="chip" onClick={() => onFocusField(f.name)}>
                  {content}
                </button>
              );
            })}
          </div>
        </div>
      ) : (
        <p className="muted" style={{ marginTop: 0, fontSize: 13 }}>
          Click any figure to see which contract terms produced it, highlighted on the PDF.
          {schedule.remeasurements.length > 0 &&
            ` Remeasured ${schedule.remeasurements.length} time${schedule.remeasurements.length > 1 ? "s" : ""} by amendments.`}
        </p>
      )}

      <div className="figures">
        {summary.map((s) => (
          <button
            key={s.label}
            className="figure"
            data-active={is(s.figure, null)}
            onClick={() =>
              onSelectFigure({
                figure: s.figure,
                period: null,
                segment: s.figure === "liability" || s.figure === "rou" ? 0 : null,
              })
            }
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
          {schedule.periods.map((p) => {
            const remeasured = remeasuredAfter.get(p.number);
            return (
              <Fragment key={p.number}>
                <tr data-segment={p.segment > 0 || undefined}>
                  <td className="faint num" style={{ padding: "0 6px" }}>
                    {p.number}
                  </td>
                  {COLUMNS.map((c) => (
                    <td key={c.figure}>
                      <button
                        className="cell-btn"
                        data-active={is(c.figure, p.number)}
                        onClick={() => onSelectFigure({ figure: c.figure, period: p.number, segment: p.segment })}
                      >
                        {c.value(p)}
                      </button>
                    </td>
                  ))}
                </tr>
                {remeasured && (
                  <tr className="remeasure-row">
                    <td colSpan={COLUMNS.length + 1}>
                      <button
                        className="remeasure"
                        data-active={is("remeasurement", p.number)}
                        onClick={() =>
                          onSelectFigure({ figure: "remeasurement", period: p.number, segment: remeasured.segment })
                        }
                      >
                        <span className="remeasure-title">
                          Agreed {formatDate(remeasured.r.effective)}
                          {remeasured.r.remeasured_on && remeasured.r.remeasured_on !== remeasured.r.effective &&
                            `, remeasured ${formatDate(remeasured.r.remeasured_on)}`}
                          {remeasured.r.terms_start && remeasured.r.terms_start !== remeasured.r.effective &&
                            `, new terms from ${formatDate(remeasured.r.terms_start)}`}{" "}
                          · {remeasured.r.filename}
                        </span>
                        <span className="num">
                          Liability {formatAmount(remeasured.r.liability_before)} →{" "}
                          {formatAmount(remeasured.r.liability_after)} · ROU {signed(remeasured.r.rou_adjustment)}
                          {toCents(remeasured.r.gain) > 0n && ` · gain ${formatAmount(remeasured.r.gain)}`}
                        </span>
                      </button>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>

      {(schedule.separate_leases ?? []).map((x) => (
        <SeparateLeaseView key={x.document_id} lease={x} />
      ))}

      {gains.some((g) => toCents(g) > 0n) && (
        <p className="term-note">
          A decrease larger than the right-of-use asset was taken to profit or loss as a gain (IFRS 16.39).
        </p>
      )}
      <div style={{ marginTop: 12, display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <button className="btn" onClick={onDownload}>
          Download Excel (schedule, journal, chain, provenance)
        </button>
        <span className="mono faint" title={`SHA-256 over the lease and every amendment, in order: ${schedule.chain_fingerprint}`}>
          chain {shortHash(schedule.chain_fingerprint)}
        </span>
      </div>
    </>
  );
}

/** Added space accounted for as its own lease (IFRS 16.44): its own liability, asset and periods. */
function SeparateLeaseView({ lease }: { lease: SeparateLease }) {
  const basis = lease.inputs.find((f) => f.name === "expansion_treatment")?.basis;
  return (
    <div className="card" style={{ marginTop: 16, padding: "12px 14px" }}>
      <div className="term-label">Separate lease (IFRS 16.44) from {lease.filename}</div>
      <p className="term-note" style={{ marginTop: 4 }}>
        Added space from {formatDate(lease.commencement)}: initial liability {formatAmount(lease.initial_liability)},
        right-of-use asset {formatAmount(lease.initial_rou_asset)}, {lease.periods.length} periods. The lease above is
        not remeasured for it.{basis ? ` Basis: ${basis}` : ""}{" "}
        <Link className="link" href={`/documents/${lease.document_id}`}>Open the amendment</Link>
      </p>
      <table className="sched">
        <thead>
          <tr>
            <th className="num">#</th>
            {COLUMNS.map((c) => (
              <th key={c.figure} className={c.figure === "end_date" ? undefined : "num"}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {lease.periods.map((p) => (
            <tr key={p.number}>
              <td className="num">{p.number}</td>
              {COLUMNS.map((c) => (
                <td key={c.figure} className={c.figure === "end_date" ? undefined : "num"}>{c.value(p)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
