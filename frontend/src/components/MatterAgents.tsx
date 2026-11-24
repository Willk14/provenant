"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import {
  phase2Api,
  saveBytes,
  type CourtNotice,
  type Diligence,
  type DocketEntry,
  type Precedent,
  type PrecedentDraft,
} from "@/lib/practice2";
import type { DocumentSummary } from "@/lib/types";

function useLoad<T>(load: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(async () => {
    try {
      setData(await load());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [load]);
  useEffect(() => {
    void reload();
  }, [reload]);
  const act = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return { data, busy, error, act, reload };
}

const PLACEHOLDER = /(\[\[[a-z][a-z0-9_]*\]\])/g;

/** Drafts filled from the firm's locked precedents. Departures from the precedent are shown to the approver. */
export function PrecedentDrafts({ matterId, open }: { matterId: string; open: boolean }) {
  const load = useCallback(() => Promise.all([phase2Api.drafts(matterId), phase2Api.precedents()]), [matterId]);
  const { data, busy, error, act } = useLoad(load);
  const [precedentId, setPrecedentId] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [include, setInclude] = useState<string[]>([]);
  if (!data) return <section className="card practice-form"><strong>Drafts from precedents</strong>{error && <div className="error-banner">{error}</div>}</section>;
  const [{ facts, drafts }, precedents] = data;
  const chosen: Precedent | undefined = precedents.find((p) => p.id === precedentId) ?? precedents[0];
  return (
    <section className="card practice-form">
      <div className="practice-row space">
        <strong>Drafts from precedents</strong>
        <Link className="link" href="/precedents">Manage precedents</Link>
      </div>
      {error && <div className="error-banner">{error}</div>}
      {!open ? <div className="faint" style={{ fontSize: 13 }}>Clear the conflict check to draft from precedents.</div>
        : precedents.length === 0 ? <div className="faint" style={{ fontSize: 13 }}>No precedents yet. A partner locks the firm&apos;s templates on the Precedents page.</div>
          : chosen && (
            <form className="practice-form flush" onSubmit={(e) => {
              e.preventDefault();
              void act(() => phase2Api.createDraft(matterId, chosen.id, values, include));
            }}>
              <div className="practice-row">
                <select className="input" value={chosen.id} aria-label="Precedent"
                  onChange={(e) => { setPrecedentId(e.target.value); setValues({}); setInclude([]); }}>
                  {precedents.map((p) => <option key={p.id} value={p.id}>{p.name} (v{p.version})</option>)}
                </select>
                {chosen.options.map((o) => (
                  <label key={o} className="team-toggle">
                    <input type="checkbox" checked={include.includes(o)}
                      onChange={(e) => setInclude(e.target.checked ? [...include, o] : include.filter((x) => x !== o))} />
                    Include {o.replace(/_/g, " ")}
                  </label>
                ))}
              </div>
              <div className="practice-fields">
                {chosen.fields.map((f) => (
                  <label key={f} className="field">{f.replace(/_/g, " ")}
                    <input className="input" value={values[f] ?? ""} placeholder={facts[f] || "leave blank to fill later"}
                      onChange={(e) => setValues({ ...values, [f]: e.target.value })} />
                  </label>
                ))}
              </div>
              <div><button className="btn btn-primary btn-sm" disabled={busy}>Fill precedent</button></div>
            </form>
          )}
      {drafts.map((d) => <DraftCard key={`${d.id}-${d.updated_at}`} draft={d} busy={busy} act={act} matterId={matterId} />)}
    </section>
  );
}

function DraftCard({ draft, busy, act, matterId }: {
  draft: PrecedentDraft;
  busy: boolean;
  act: (a: () => Promise<unknown>) => Promise<void>;
  matterId: string;
}) {
  const [text, setText] = useState(draft.text);
  const [note, setNote] = useState("");
  const [editing, setEditing] = useState(false);
  return (
    <div className="practice-draft">
      <div className="practice-row space">
        <b>{draft.precedent} v{draft.version}</b>
        <span className={`pill ${draft.status === "approved" ? "pill-ok" : "pill-warn"}`}>{draft.status === "approved" ? "Approved" : draft.status === "returned" ? "Returned" : "To review"}</span>
      </div>
      {editing ? (
        <textarea className="input" rows={14} style={{ height: "auto" }} value={text} onChange={(e) => setText(e.target.value)} aria-label="Draft text" />
      ) : (
        <div className="draft-paper"><p style={{ whiteSpace: "pre-wrap", margin: 0 }}>{draft.text.split(PLACEHOLDER).map((part, i) => i % 2 ? <mark key={i} className="draft-placeholder">{part}</mark> : part)}</p></div>
      )}
      {draft.deviations.length > 0 && (
        <div className="practice-deviations">
          <b>{draft.deviations.length} departure{draft.deviations.length === 1 ? "" : "s"} from the precedent</b> (a partner must approve)
          {draft.deviations.map((d, i) => (
            <div key={i} className="practice-deviation">
              <span className="pill pill-warn">{d.kind}</span>
              {d.standard && <del>{d.standard}</del>}
              {d.edited && <ins>{d.edited}</ins>}
            </div>
          ))}
        </div>
      )}
      <div className="faint" style={{ fontSize: 12 }}>
        Made by {draft.created_by}{draft.edited_by ? ` · edited by ${draft.edited_by}` : ""}{draft.decided_by ? ` · ${draft.status} by ${draft.decided_by}${draft.decision_note ? `: ${draft.decision_note}` : ""}` : ""}
      </div>
      {draft.status !== "approved" && (
        <div className="practice-row">
          {editing ? (
            <>
              <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => void act(() => phase2Api.editDraft(matterId, draft.id, text)).then(() => setEditing(false))}>Save</button>
              <button className="btn btn-sm btn-ghost" onClick={() => setEditing(false)}>Cancel</button>
            </>
          ) : <button className="btn btn-sm" onClick={() => setEditing(true)}>Edit</button>}
          {draft.status === "ready" && !editing && (
            <>
              <input className="input grow" value={note} onChange={(e) => setNote(e.target.value)} aria-label="Decision note"
                placeholder={draft.deviations.length ? "Why the departures are acceptable" : "Note (needed to return)"} />
              <button className="btn btn-sm" disabled={busy || draft.unfilled.length > 0}
                onClick={() => void act(() => phase2Api.decideDraft(matterId, draft.id, "approved", note))}>Approve</button>
              <button className="btn btn-sm" disabled={busy || note.trim().length < 5}
                onClick={() => void act(() => phase2Api.decideDraft(matterId, draft.id, "returned", note))}>Return</button>
            </>
          )}
        </div>
      )}
      {draft.status === "approved" && (
        <div><button className="btn btn-sm" onClick={() => void navigator.clipboard.writeText(draft.text)}>Copy text</button></div>
      )}
    </div>
  );
}

/** Court notices read into proposed events; deadlines are computed from the firm's rules only once confirmed. */
export function MatterDocket({ matterId }: { matterId: string }) {
  const load = useCallback(() => phase2Api.matterDocket(matterId), [matterId]);
  const { data, busy, error, act } = useLoad(load);
  const [text, setText] = useState("");
  const [manual, setManual] = useState({ title: "", due: "", basis: "" });
  return (
    <section className="card practice-form">
      <div className="practice-row space">
        <strong>Docket</strong>
        <Link className="link" href="/docket">Firm docket and rules</Link>
      </div>
      {error && <div className="error-banner">{error}</div>}
      <form className="practice-form flush" onSubmit={(e) => {
        e.preventDefault();
        void act(async () => { await phase2Api.addNotice(matterId, text); setText(""); });
      }}>
        <textarea className="input" rows={3} style={{ height: "auto" }} value={text} onChange={(e) => setText(e.target.value)}
          aria-label="Court notice text" placeholder="Paste a court notice or registry email. The reader proposes the event and dates; you confirm." />
        <div><button className="btn btn-sm" disabled={busy || text.trim().length < 20}>Read notice</button></div>
      </form>
      {data?.notices.filter((n) => n.status === "new").map((n) => <NoticeCard key={n.id} notice={n} busy={busy} act={act} matterId={matterId} />)}
      {data && data.entries.length > 0 && (
        <table className="table">
          <thead><tr><th>Deadline</th><th>Due</th><th>Basis</th><th /></tr></thead>
          <tbody>{data.entries.map((e: DocketEntry) => (
            <tr key={e.id}>
              <td>{e.title}</td><td className="num">{formatDate(e.due_on)}</td><td className="muted" style={{ fontSize: 12 }}>{e.basis}</td>
              <td>{e.status === "done" ? <span className="pill pill-ok">Done</span>
                : <button className="btn btn-sm" disabled={busy} onClick={() => void act(() => phase2Api.markDone(e.id))}>Done</button>}</td>
            </tr>))}</tbody>
        </table>
      )}
      <form className="practice-row" onSubmit={(e) => {
        e.preventDefault();
        void act(async () => { await phase2Api.addDeadline(matterId, manual.title, manual.due, manual.basis); setManual({ title: "", due: "", basis: "" }); });
      }}>
        <input className="input grow" required placeholder="Add a deadline by hand" value={manual.title} onChange={(e) => setManual({ ...manual, title: e.target.value })} aria-label="Deadline title" />
        <input className="input" type="date" required value={manual.due} onChange={(e) => setManual({ ...manual, due: e.target.value })} aria-label="Due date" />
        <input className="input" required placeholder="Basis" value={manual.basis} onChange={(e) => setManual({ ...manual, basis: e.target.value })} aria-label="Deadline basis" />
        <button className="btn btn-sm" disabled={busy}>Add</button>
      </form>
    </section>
  );
}

function NoticeCard({ notice, busy, act, matterId }: {
  notice: CourtNotice;
  busy: boolean;
  act: (a: () => Promise<unknown>) => Promise<void>;
  matterId: string;
}) {
  const [event, setEvent] = useState(notice.reading.event ?? "");
  const [day, setDay] = useState(notice.reading.dates[0] ?? "");
  const [caseNumber, setCaseNumber] = useState(notice.reading.case_number ?? "");
  return (
    <div className="practice-draft">
      <b>Notice to confirm</b>
      <div className="faint" style={{ fontSize: 12 }}>
        Read: {notice.reading.event ? `“${notice.reading.evidence.event}”` : "no event found"}
        {notice.reading.dates.length ? ` · dates ${notice.reading.dates.map((d) => `“${notice.reading.evidence[d]}”`).join(", ")}` : ""}
      </div>
      <div className="practice-row">
        <input className="input" value={event} onChange={(e) => setEvent(e.target.value)} placeholder="Event, e.g. judgment" aria-label="Event" />
        <input className="input" type="date" value={day} onChange={(e) => setDay(e.target.value)} aria-label="Event date" />
        <input className="input grow" value={caseNumber} onChange={(e) => setCaseNumber(e.target.value)} placeholder="Case number" aria-label="Case number" />
        <button className="btn btn-sm btn-primary" disabled={busy || !event || !day}
          onClick={() => void act(() => phase2Api.confirmNotice(matterId, notice.id, event, day, caseNumber))}>Confirm and docket</button>
      </div>
    </div>
  );
}

const CELL_WORD = { not_found: "Not found", not_reviewed: "Not reviewed", reviewing: "Reviewing…", failed: "Failed" } as const;

/** The matter's data room: contracts reviewed in bulk and shown against every playbook topic. */
export function DueDiligence({ matterId }: { matterId: string }) {
  const load = useCallback(() => Promise.all([phase2Api.diligence(matterId), api.documents()]), [matterId]);
  const { data, busy, error, act, reload } = useLoad(load);
  const [selected, setSelected] = useState<string[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const working = !!data?.[0].rows.some((r) => Object.values(r.cells).some((c) => c.state === "reviewing"));
  useEffect(() => {
    if (!working) return;
    const timer = setInterval(() => void reload(), 2000);
    return () => clearInterval(timer);
  }, [working, reload]);
  if (!data) return <section className="card practice-form"><strong>Due diligence</strong>{error && <div className="error-banner">{error}</div>}</section>;
  const [matrix, documents]: [Diligence, DocumentSummary[]] = data;
  const linked = new Set(matrix.rows.map((r) => r.document_id));
  const available = documents.filter((d) => d.kind !== "amendment" && !linked.has(d.id));
  return (
    <section className="card practice-form">
      <div className="practice-row space">
        <strong>Due diligence</strong>
        <span className="faint" style={{ fontSize: 12 }}>{matrix.reviewed} of {matrix.documents} contracts reviewed</span>
      </div>
      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="callout callout-ok">{notice}</div>}
      {available.length > 0 && (
        <div className="practice-row">
          <select multiple className="input grow" size={Math.min(4, available.length)} aria-label="Contracts to add"
            value={selected} onChange={(e) => setSelected([...e.target.selectedOptions].map((o) => o.value))}>
            {available.map((d) => <option key={d.id} value={d.id}>{d.filename}</option>)}
          </select>
          <button className="btn btn-sm" disabled={busy || selected.length === 0}
            onClick={() => void act(async () => { await phase2Api.link(matterId, selected); setSelected([]); })}>Add to data room</button>
        </div>
      )}
      <div className="practice-row">
        <button className="btn btn-sm btn-primary" disabled={busy || matrix.documents === 0}
          onClick={() => void act(async () => {
            const r = await phase2Api.reviewAll(matterId);
            setNotice(`${r.started} review${r.started === 1 ? "" : "s"} queued.${r.skipped.length ? ` Skipped: ${r.skipped.map((x) => `${x.filename} (${x.reason})`).join("; ")}` : ""}`);
          })}>Review every contract</button>
        <button className="btn btn-sm" disabled={matrix.documents === 0}
          onClick={() => void phase2Api.diligenceXlsx(matterId).then((b) => saveBytes(b, `due-diligence-${matrix.matter}.xlsx`,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))}>Export Excel</button>
      </div>
      {matrix.rows.length > 0 && (
        <div className="table-scroll">
          <table className="table diligence">
            <thead><tr><th>Contract</th>{matrix.topics.map((t) => <th key={t.key}>{t.label}</th>)}</tr></thead>
            <tbody>{matrix.rows.map((r) => (
              <tr key={r.document_id}>
                <td><Link className="link" href={`/documents/${r.document_id}?tab=review`}>{r.filename}</Link>
                  <div className="faint" style={{ fontSize: 11 }}>{r.deviations} deviation{r.deviations === 1 ? "" : "s"}</div></td>
                {matrix.topics.map((t) => {
                  const c = r.cells[t.key];
                  return (
                    <td key={t.key} className={c.deviation ? "diligence-bad" : ""} title={c.quote}>
                      {c.state === "finding" ? <><b>{c.severity}</b> {c.title}<div className="faint">p. {c.page}</div></> : <span className="faint">{CELL_WORD[c.state]}</span>}
                    </td>
                  );
                })}
              </tr>))}</tbody>
          </table>
        </div>
      )}
    </section>
  );
}
