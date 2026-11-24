"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { formatDate } from "@/lib/format";
import { phase2Api, saveBytes, type DeadlineRule, type DocketEntry } from "@/lib/practice2";
import { usePersona } from "@/lib/usePersona";

const BLANK = { name: "", trigger: "judgment", days: "", months: "", counting: "calendar", roll_forward: true, basis: "" };

export default function DocketPage() {
  const persona = usePersona();
  const [entries, setEntries] = useState<DocketEntry[]>([]);
  const [rules, setRules] = useState<DeadlineRule[]>([]);
  const [holidays, setHolidays] = useState<{ id: string; day: string; name: string }[]>([]);
  const [rule, setRule] = useState(BLANK);
  const [holiday, setHoliday] = useState({ day: "", name: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [e, r, h] = await Promise.all([phase2Api.docket(), phase2Api.rules(), phase2Api.holidays()]);
      setEntries(e);
      setRules(r);
      setHolidays(h);
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);
  useEffect(() => {
    if (persona) void load();
  }, [persona, load]);

  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
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
  const today = new Date().toISOString().slice(0, 10);
  return (
    <main className="page">
      <Link href="/matters" className="link">← Matters</Link>
      <div className="page-head">
        <div>
          <h1>Docket</h1>
          <p className="muted">
            Every open deadline across the firm&apos;s matters. Deadlines come from court notices a person confirmed and
            the firm&apos;s own rules, each with its stated basis.
          </p>
        </div>
        <button className="btn btn-sm" disabled={entries.length === 0}
          onClick={() => void phase2Api.calendar().then((b) => saveBytes(b, "provenant-docket.ics", "text/calendar")).catch((e: Error) => setError(e.message))}>
          Add to calendar (.ics)
        </button>
      </div>
      {error && <div className="error-banner">{error}</div>}
      <div className="card">
        {entries.length === 0 ? <div className="empty">No open deadlines.</div> : (
          <table className="table">
            <thead><tr><th>Due</th><th>Matter</th><th>Deadline</th><th>Basis</th><th /></tr></thead>
            <tbody>{entries.map((e) => (
              <tr key={e.id} className={e.due_on < today ? "docket-overdue" : ""}>
                <td className="num">{formatDate(e.due_on)}{e.due_on < today && <div><span className="pill pill-bad">Overdue</span></div>}</td>
                <td><Link className="link" href={`/matters/${e.matter_id}`}>{e.matter}</Link></td>
                <td>{e.title}</td>
                <td className="muted" style={{ fontSize: 12 }}>{e.basis}</td>
                <td><button className="btn btn-sm" disabled={busy} onClick={() => void act(() => phase2Api.markDone(e.id))}>Done</button></td>
              </tr>))}</tbody>
          </table>
        )}
      </div>

      <div className="practice-columns">
        <section className="card practice-form">
          <strong>Deadline rules</strong>
          <span className="faint" style={{ fontSize: 12 }}>
            Periods are the firm&apos;s inputs, with their basis. Provenant does not decide limitation law.
          </span>
          <ul className="practice-list">{rules.map((r) => (
            <li key={r.id}><b>{r.name}:</b> {r.describe}. <span className="faint">{r.basis}</span>
              {partner && <button className="btn btn-sm btn-ghost" disabled={busy} onClick={() => void act(() => phase2Api.retireRule(r.id))}>Retire</button>}</li>
          ))}</ul>
          {partner && (
            <form className="practice-form flush" onSubmit={(e) => {
              e.preventDefault();
              void act(async () => {
                await phase2Api.addRule({ name: rule.name, trigger: rule.trigger, days: Number(rule.days || 0),
                  months: Number(rule.months || 0), counting: rule.counting as DeadlineRule["counting"],
                  roll_forward: rule.roll_forward, basis: rule.basis });
                setRule(BLANK);
              });
            }}>
              <div className="practice-row">
                <input className="input grow" required placeholder="Name, e.g. Notice of appeal" value={rule.name}
                  onChange={(e) => setRule({ ...rule, name: e.target.value })} aria-label="Rule name" />
                <select className="input" value={rule.trigger} onChange={(e) => setRule({ ...rule, trigger: e.target.value })} aria-label="Trigger event">
                  {["judgment", "ruling", "decree", "order", "hearing", "mention", "service", "filing"].map((t) => <option key={t} value={t}>after {t}</option>)}
                </select>
              </div>
              <div className="practice-row">
                <input className="input" type="number" min={0} placeholder="Days" value={rule.days} onChange={(e) => setRule({ ...rule, days: e.target.value })} aria-label="Days" />
                <input className="input" type="number" min={0} placeholder="Months" value={rule.months} onChange={(e) => setRule({ ...rule, months: e.target.value })} aria-label="Months" />
                <select className="input" value={rule.counting} onChange={(e) => setRule({ ...rule, counting: e.target.value })} aria-label="Counting">
                  <option value="calendar">calendar days</option>
                  <option value="court_days">court days</option>
                </select>
                <label className="team-toggle"><input type="checkbox" checked={rule.roll_forward}
                  onChange={(e) => setRule({ ...rule, roll_forward: e.target.checked })} /> next working day</label>
              </div>
              <input className="input" required placeholder="Basis: the statute, rule or practice direction" value={rule.basis}
                onChange={(e) => setRule({ ...rule, basis: e.target.value })} aria-label="Rule basis" />
              <div><button className="btn btn-sm" disabled={busy}>Add rule</button></div>
            </form>
          )}
        </section>
        <section className="card practice-form">
          <strong>Court holidays</strong>
          <ul className="practice-list">{holidays.map((h) => <li key={h.id}>{formatDate(h.day)}: {h.name}</li>)}</ul>
          {partner && (
            <form className="practice-row" onSubmit={(e) => {
              e.preventDefault();
              void act(async () => { await phase2Api.addHoliday(holiday.day, holiday.name); setHoliday({ day: "", name: "" }); });
            }}>
              <input className="input" type="date" required value={holiday.day} onChange={(e) => setHoliday({ ...holiday, day: e.target.value })} aria-label="Holiday date" />
              <input className="input grow" required placeholder="Name" value={holiday.name} onChange={(e) => setHoliday({ ...holiday, name: e.target.value })} aria-label="Holiday name" />
              <button className="btn btn-sm" disabled={busy}>Add</button>
            </form>
          )}
        </section>
      </div>
    </main>
  );
}
