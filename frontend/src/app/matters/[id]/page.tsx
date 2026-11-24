"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { formatDate } from "@/lib/format";
import { practiceApi, STATUS_PILL, STATUS_WORD, type Matter } from "@/lib/practice";
import { usePersona } from "@/lib/usePersona";

const ITEMS = [
  { key: "engagement_letter", label: "Signed engagement letter" },
  { key: "client_id", label: "Verified client identity document" },
  { key: "beneficial_ownership", label: "Beneficial ownership record" },
  { key: "source_of_funds", label: "Source of funds evidence" },
];
const KIND_WORD = { screening: "Screening list", adverse: "Adverse party", same_side: "Same side" } as const;
const KIND_PILL = { screening: "pill-bad", adverse: "pill-bad", same_side: "" } as const;

export default function MatterPage() {
  const { id } = useParams<{ id: string }>();
  const persona = usePersona();
  const [matter, setMatter] = useState<Matter | null>(null);
  const [note, setNote] = useState("");
  const [item, setItem] = useState(ITEMS[0].key);
  const [expires, setExpires] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setMatter(await practiceApi.matter(id));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [id]);

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

  async function download(requirementId: string, filename: string) {
    const bytes = await practiceApi.requirementFile(id, requirementId);
    const url = URL.createObjectURL(new Blob([bytes]));
    Object.assign(document.createElement("a"), { href: url, download: filename }).click();
    URL.revokeObjectURL(url);
  }

  if (!matter) return <main className="page">{error ? <div className="error-banner">{error}</div> : <div className="empty">Loading…</div>}</main>;
  const hits = matter.conflict?.hits ?? [];
  return (
    <main className="page">
      <Link href="/matters" className="link">← Matters</Link>
      <div className="page-head">
        <div>
          <h1>{matter.reference} · {matter.title}</h1>
          <p className="muted">{matter.matter_type} · client is {matter.client_kind === "person" ? "a person" : "an organisation"} · opened by {matter.created_by} on {formatDate(matter.created_at)}</p>
        </div>
        <span className={`pill ${STATUS_PILL[matter.status]}`}>{STATUS_WORD[matter.status]}</span>
      </div>
      {error && <div className="error-banner">{error}</div>}

      <section className="card practice-form">
        <strong>Parties</strong>
        <ul className="practice-list">{matter.parties.map((p, i) => (
          <li key={i}><b>{p.name}</b> <span className="faint">{p.role.replace("_", " ")}{p.identifier ? ` · ${p.identifier}` : ""}</span></li>
        ))}</ul>
      </section>

      <section className="card practice-form">
        <div className="practice-row space">
          <strong>Conflict check</strong>
          {matter.conflict && <span className="faint" style={{ fontSize: 12 }}>
            {matter.conflict.compared} names compared by {matter.conflict.run_by} on {formatDate(matter.conflict.run_at)}</span>}
        </div>
        {hits.length === 0 ? <div className="callout callout-ok">No possible matches.{matter.status === "conflict_review" ? " A person other than the one who opened it still has to clear the matter." : ""}</div> : (
          <table className="table">
            <thead><tr><th>Party</th><th>Possible match</th><th>Why</th><th>Kind</th></tr></thead>
            <tbody>{hits.map((h, i) => (
              <tr key={i}>
                <td>{h.party}<div className="faint" style={{ fontSize: 12 }}>{h.party_role}</div></td>
                <td>{h.matched}<div className="faint" style={{ fontSize: 12 }}>{h.matched_role} · {h.source}</div></td>
                <td className="muted">{h.reason}</td>
                <td><span className={`pill ${KIND_PILL[h.kind]}`}>{KIND_WORD[h.kind]}</span></td>
              </tr>
            ))}</tbody>
          </table>
        )}
        {matter.status === "conflict_review" ? (
          <div className="practice-row">
            <input className="input grow" value={note} onChange={(e) => setNote(e.target.value)} aria-label="Reasons"
              placeholder={hits.length ? "Reasons (needed when there are possible matches)" : "Note (optional)"} />
            <button className="btn btn-primary btn-sm" disabled={busy}
              onClick={() => void act(() => practiceApi.decideConflict(id, "cleared", note))}>Clear and open</button>
            <button className="btn btn-sm" disabled={busy || note.trim().length < 5}
              onClick={() => void act(() => practiceApi.decideConflict(id, "conflict", note))}>Decline: conflict</button>
            <button className="btn btn-sm btn-ghost" disabled={busy} onClick={() => void act(() => practiceApi.rerunConflict(id))}>Run again</button>
          </div>
        ) : matter.conflict?.decided_by && (
          <div className="faint" style={{ fontSize: 13 }}>Decided by {matter.conflict.decided_by}{matter.conflict.decision_note ? `: ${matter.conflict.decision_note}` : ""}</div>
        )}
      </section>

      <section className="card practice-form">
        <div className="practice-row space">
          <strong>KYC and file</strong>
          <span className={`pill ${matter.file_gaps.length ? "pill-warn" : "pill-ok"}`}>
            {matter.file_gaps.length ? `${matter.file_gaps.length} gap${matter.file_gaps.length === 1 ? "" : "s"}` : "Complete"}</span>
        </div>
        {matter.file_gaps.length > 0 && <ul className="practice-list">{matter.file_gaps.map((g) => <li key={g.item}><b>{g.problem}:</b> {g.detail}</li>)}</ul>}
        {matter.requirements.map((r) => (
          <div key={r.id} className="practice-row space practice-item">
            <span><b>{r.label}</b> <span className="faint">{r.filename}{r.expires_on ? ` · expires ${formatDate(r.expires_on)}` : ""} · from {r.provided_by}</span></span>
            <span className="practice-row">
              <button className="btn btn-sm btn-ghost" onClick={() => void download(r.id, r.filename).catch((e: Error) => setError(e.message))}>Download</button>
              {r.status === "verified"
                ? <span className="pill pill-ok">Verified by {r.verified_by}</span>
                : <button className="btn btn-sm" disabled={busy} onClick={() => void act(() => practiceApi.verifyRequirement(id, r.id))}>Verify</button>}
            </span>
          </div>
        ))}
        <form className="practice-row" onSubmit={(e) => {
          e.preventDefault();
          const file = (e.currentTarget.elements.namedItem("evidence") as HTMLInputElement).files?.[0];
          if (file) void act(() => practiceApi.addRequirement(id, item, file, expires));
        }}>
          <select className="input" value={item} onChange={(e) => setItem(e.target.value)} aria-label="File item">
            {ITEMS.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
          </select>
          <input name="evidence" type="file" required aria-label="Evidence file" />
          <input className="input" type="date" value={expires} onChange={(e) => setExpires(e.target.value)} aria-label="Expires on (optional)" />
          <button className="btn btn-sm" disabled={busy}>Add to file</button>
        </form>
      </section>

      <section className="card practice-form">
        <strong>History</strong>
        <ul className="practice-list">{matter.events.map((e, i) => (
          <li key={i}>{formatDate(e.at)} · <b>{e.action.replace(/_/g, " ")}</b> by {e.actor}{e.note ? `: ${e.note}` : ""}</li>
        ))}</ul>
      </section>
    </main>
  );
}
