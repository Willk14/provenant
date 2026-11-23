"use client";

import { useCallback, useEffect, useState } from "react";

import { ContractSearch } from "@/components/ContractSearch";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import {
  fieldLabel,
  type DocumentSummary,
  type LegalCitation,
  type LegalFinding,
  type LegalPlaybook,
  type LegalReview,
} from "@/lib/types";

const SEVERITY_PILL = { high: "pill-bad", medium: "pill-warn", low: "", info: "" } as const;
const SEVERITY_WORD = { high: "High", medium: "Medium", low: "Low", info: "To read" } as const;

interface Props {
  doc: DocumentSummary;
  onCite: (id: string, citation: LegalCitation) => void;
}

export function LegalReviewPanel({ doc, onCite }: Props) {
  const [review, setReview] = useState<LegalReview | null>(null);
  const [playbook, setPlaybook] = useState<LegalPlaybook | null>(null);
  const [editing, setEditing] = useState(false);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [reviews, book] = await Promise.all([api.legalReviews(doc.id), api.legalPlaybook()]);
      setPlaybook(book);
      setReview(reviews.length ? await api.legalReview(doc.id, reviews[0].id) : null);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [doc.id]);

  useEffect(() => {
    void load();
  }, [load]);

  const working = !!review && (["queued", "running"].includes(review.status)
    || review.questions.some((q) => ["queued", "running"].includes(q.status)));
  useEffect(() => {
    if (!working) return;
    const timer = setInterval(load, 1500);
    return () => clearInterval(timer);
  }, [working, load]);

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

  const label = (key: string) => playbook?.topics.find((t) => t.key === key)?.label ?? key;
  const running = review?.status === "queued" || review?.status === "running";
  const baseline = review?.agent?.startsWith("baseline");
  const open = review?.findings.filter((f) => !f.decision).length ?? 0;

  return (
    <>
      {error && <div className="error-banner">{error}</div>}
      <div className="callout">
        <strong>Contract review</strong>
        <span className="muted">
          Every finding quotes the contract. A lawyer decides what it means; this is not legal advice.
        </span>
        <div className="chips" style={{ marginTop: 8 }}>
          <button className="btn btn-sm" disabled={busy || running} onClick={() => void act(() => api.startLegalReview(doc.id))}>
            {running ? "Reviewing…" : review ? "Run again" : "Review contract"}
          </button>
          <button className="btn btn-sm btn-ghost" onClick={() => setEditing((v) => !v)} aria-expanded={editing}>
            Firm playbook
          </button>
        </div>
      </div>

      {editing && playbook && (
        <PlaybookEditor key={playbook.sha256} playbook={playbook} busy={busy}
          onSave={(positions) => void act(() => api.saveLegalPlaybook(positions))} />
      )}

      <ContractSearch doc={doc} onCite={onCite} />

      {review?.status === "failed" && (
        <div className="error-banner">The review did not finish: {review.error}. Nothing from it was saved.</div>
      )}

      {review?.status === "done" && (
        <>
          {baseline && (
            <div className="callout callout-warn">
              <strong>Keyword scan</strong>
              It shows where each topic is mentioned and does not assess the clauses.
            </div>
          )}
          <div className="term" style={{ cursor: "default", marginTop: 14 }}>
            <div className="term-head">
              <span className="term-label">Summary</span>
              <span className="faint" style={{ fontSize: 12 }}>{open} to decide</span>
            </div>
            <div className="term-note">{review.summary}</div>
            <div className="term-meta">
              <span>{review.agent} · {review.playbook_version} · requested by {review.requested_by}
                {review.finished_at ? ` on ${formatDate(review.finished_at)}` : ""}</span>
            </div>
          </div>

          {review.findings.map((f) => (
            <FindingCard key={f.id} finding={f} doc={doc} label={label(f.topic)} busy={busy} onCite={onCite}
              onDecide={(decision, note) => void act(() => api.decideLegalFinding(doc.id, review.id, f.id, decision, note))} />
          ))}

          {review.not_found.length > 0 && (
            <div className="term" style={{ cursor: "default", marginTop: 14 }}>
              <div className="term-head"><span className="term-label">Not found in the contract</span></div>
              <div className="term-note">{review.not_found.map(label).join(", ")}. A lawyer should confirm these are absent.</div>
            </div>
          )}

          <div className="term" style={{ cursor: "default", marginTop: 14 }}>
            <div className="term-head"><span className="term-label">Ask about this contract</span></div>
            {review.questions.map((q) => (
              <div key={q.id} style={{ marginBottom: 10 }}>
                <div><b>{q.question}</b> <span className="faint">{q.asked_by}</span></div>
                {q.status === "failed" ? <div className="error-banner">{q.error}</div>
                  : q.answer ? <div className="term-note">{q.answer}</div>
                  : <div className="term-note">Reading…</div>}
                <div className="chips">
                  {q.citations.map((c, i) => <CitationChip key={i} id={`${q.id}-${i}`} citation={c} doc={doc} onCite={onCite} />)}
                </div>
              </div>
            ))}
            <form className="term-actions" onSubmit={(e) => {
              e.preventDefault();
              void act(async () => {
                await api.askLegalQuestion(doc.id, review.id, question);
                setQuestion("");
              });
            }}>
              <input className="input" value={question} onChange={(e) => setQuestion(e.target.value)}
                placeholder="Can the lender cancel on a change of control?" aria-label="Question about this contract" />
              <button className="btn btn-sm" disabled={busy || question.trim().length < 5}>Ask</button>
            </form>
          </div>
        </>
      )}
    </>
  );
}

function CitationChip({ id, citation, doc, onCite }: {
  id: string;
  citation: LegalCitation;
  doc: DocumentSummary;
  onCite: Props["onCite"];
}) {
  const here = citation.document_id === doc.id;
  return (
    <button className="chip" disabled={!here} onClick={() => onCite(id, citation)}
      title={here ? "Show on the PDF" : `In ${citation.filename}`}>
      <span className="faint">{here ? "Page" : `${citation.filename}, page`}</span> <b>{citation.page}</b>
    </button>
  );
}

function FindingCard({ finding: f, doc, label, busy, onCite, onDecide }: {
  finding: LegalFinding;
  doc: DocumentSummary;
  label: string;
  busy: boolean;
  onCite: Props["onCite"];
  onDecide: (decision: "accepted" | "rejected", note: string) => void;
}) {
  const [note, setNote] = useState("");
  return (
    <div className="term" style={{ cursor: "default", marginTop: 14 }}>
      <div className="term-head">
        <span className="term-label">{f.title}</span>
        <span className={`pill ${SEVERITY_PILL[f.severity]}`}>{SEVERITY_WORD[f.severity]}</span>
      </div>
      <div className="faint" style={{ fontSize: 12 }}>{label}</div>
      <blockquote style={{ margin: "8px 0", paddingLeft: 10, borderLeft: "2px solid var(--line, currentColor)" }}>
        {f.citation.quote}
      </blockquote>
      <div className="chips"><CitationChip id={f.id} citation={f.citation} doc={doc} onCite={onCite} /></div>
      <div className="term-note">{f.explanation}</div>
      {f.suggestion && <div className="term-note"><b>To consider:</b> {f.suggestion}</div>}
      {f.proposed_term && (
        <div className="term-note">
          <b>Proposed term:</b> {fieldLabel(f.proposed_term, doc.kind)} = {f.proposed_value}. Accepting sends it for review.
        </div>
      )}
      {f.decision ? (
        <div className="term-meta">
          <span>
            {f.decision === "accepted" ? "Accepted" : "Rejected"} by {f.decided_by}
            {f.decision_note ? `: ${f.decision_note}` : ""}
            {f.decision === "accepted" && f.proposed_term
              ? f.field_id ? " · term waiting for review" : " · the contract already has this term, so it was not changed"
              : ""}
          </span>
        </div>
      ) : (
        <div className="term-actions">
          <input className="input" value={note} onChange={(e) => setNote(e.target.value)}
            placeholder="Note (needed to reject)" aria-label={`Note on ${f.title}`} />
          <button className="btn btn-sm" disabled={busy} onClick={() => onDecide("accepted", note)}>Accept</button>
          <button className="btn btn-sm" disabled={busy || note.trim().length < 5} onClick={() => onDecide("rejected", note)}>Reject</button>
        </div>
      )}
    </div>
  );
}

function PlaybookEditor({ playbook, busy, onSave }: {
  playbook: LegalPlaybook;
  busy: boolean;
  onSave: (positions: Record<string, string>) => void;
}) {
  const [positions, setPositions] = useState(() => Object.fromEntries(playbook.topics.map((t) => [t.key, t.position])));
  const changed = Object.fromEntries(playbook.topics
    .filter((t) => !t.standard || positions[t.key] !== t.position)
    .map((t) => [t.key, positions[t.key]]));
  return (
    <div className="term" style={{ cursor: "default", marginTop: 14 }}>
      <div className="term-head">
        <span className="term-label">Firm playbook</span>
        <span className="faint" style={{ fontSize: 12 }}>a partner can change it</span>
      </div>
      {playbook.topics.map((t) => (
        <label key={t.key} style={{ display: "block", marginBottom: 8, fontSize: 12 }}>
          {t.label}
          <textarea className="input" rows={2} style={{ width: "100%", height: "auto" }} value={positions[t.key]}
            onChange={(e) => setPositions({ ...positions, [t.key]: e.target.value })} />
        </label>
      ))}
      <button className="btn btn-sm" disabled={busy} onClick={() => onSave(changed)}>Save positions</button>
    </div>
  );
}
