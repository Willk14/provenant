"use client";

import { useCallback, useEffect, useState } from "react";

import { CitationChip } from "@/components/LegalReviewPanel";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { ContractDraft, DocumentSummary, DraftKind, LegalCitation } from "@/lib/types";

const KINDS: { value: DraftKind; label: string }[] = [
  { value: "notice", label: "Notice" },
  { value: "amendment", label: "Amendment" },
  { value: "letter", label: "Letter" },
];
const STATUS = {
  queued: ["Queued", ""], running: ["Drafting…", ""], failed: ["Failed", "pill-bad"], ready: ["To review", "pill-warn"],
  returned: ["Returned", "pill-warn"], approved: ["Approved", "pill-ok"],
} as const;
const ACTION = { generated: "Drafted", edited: "Edited", returned: "Returned", approved: "Approved" } as const;
const PLACEHOLDER = /(\[\[[^[\]\n]{2,120}\]\])/g;

const hasPlaceholder = (text: string) => new RegExp(PLACEHOLDER.source).test(text);

function Wording({ text }: { text: string }) {
  return (
    <>
      {text.split(PLACEHOLDER).map((part, i) => (i % 2 ? <mark key={i} className="draft-placeholder">{part}</mark> : part))}
    </>
  );
}

interface Props {
  doc: DocumentSummary;
  onCite: (id: string, citation: LegalCitation) => void;
}

export function DraftingPanel({ doc, onCite }: Props) {
  const [drafts, setDrafts] = useState<ContractDraft[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [kind, setKind] = useState<DraftKind>("notice");
  const [instructions, setInstructions] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setDrafts(await api.contractDrafts(doc.id));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [doc.id]);

  useEffect(() => {
    void load();
  }, [load]);

  const working = drafts.some((d) => d.status === "queued" || d.status === "running");
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

  const draft = drafts.find((d) => d.id === selectedId) ?? drafts[0];

  return (
    <>
      {error && <div className="error-banner">{error}</div>}
      <div className="callout">
        <strong>Paralegal drafting</strong>
        <span className="muted">
          Prepares a first draft from this contract. Figures must trace to the contract or your instructions. A lawyer
          must still check its legal effect, and Provenant never sends it.
        </span>
      </div>

      <form className="term" style={{ cursor: "default", marginTop: 14 }} onSubmit={(e) => {
        e.preventDefault();
        void act(async () => {
          const started = await api.startContractDraft(doc.id, kind, instructions);
          setSelectedId(started.id);
          setInstructions("");
        });
      }}>
        <div className="term-head"><span className="term-label">New draft</span></div>
        <label className="field">
          Type
          <select className="input" value={kind} onChange={(e) => setKind(e.target.value as DraftKind)}>
            {KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
          </select>
        </label>
        <label className="field">
          What must it do?
          <textarea className="input" rows={3} style={{ height: "auto" }} value={instructions} maxLength={2000}
            onChange={(e) => setInstructions(e.target.value)}
            placeholder="Ask the borrower to deliver the overdue compliance certificate for the quarter to 30 June 2026." />
        </label>
        <div className="term-actions">
          <button className="btn btn-sm btn-primary" disabled={busy || working || instructions.trim().length < 10}>
            {working ? "Drafting…" : "Prepare draft"}
          </button>
        </div>
      </form>

      {drafts.length > 1 && (
        <label className="field" style={{ marginTop: 14 }}>
          Drafts
          <select className="input" value={draft?.id} onChange={(e) => setSelectedId(e.target.value)}>
            {drafts.map((d) => (
              <option key={d.id} value={d.id}>
                {d.title ?? d.kind} · {STATUS[d.status][0]} · {formatDate(d.created_at)}
              </option>
            ))}
          </select>
        </label>
      )}

      {draft && <DraftCard key={`${draft.id}-${draft.version}-${draft.status}`} draft={draft} doc={doc} busy={busy}
        onCite={onCite} act={act} />}
    </>
  );
}

function DraftCard({ draft, doc, busy, onCite, act }: {
  draft: ContractDraft;
  doc: DocumentSummary;
  busy: boolean;
  onCite: Props["onCite"];
  act: (action: () => Promise<unknown>) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(draft.title ?? "");
  const [texts, setTexts] = useState(() => draft.paragraphs.map((p) => p.text));
  const [note, setNote] = useState("");
  const [copied, setCopied] = useState(false);

  if (draft.status === "failed") {
    return <div className="error-banner">The draft did not finish: {draft.error}. Nothing from it was saved.</div>;
  }
  if (draft.status === "queued" || draft.status === "running") {
    return <div className="callout" style={{ marginTop: 14 }}><strong>Reading the contract and drafting…</strong></div>;
  }
  const open = draft.paragraphs.filter((p) => hasPlaceholder(p.text)).length + (hasPlaceholder(draft.title ?? "") ? 1 : 0);
  const returned = [...draft.history].reverse().find((e) => e.action === "returned");
  const plain = [draft.title, ...draft.paragraphs.map((p) => p.text)].join("\n\n");

  return (
    <div className="term" style={{ cursor: "default", marginTop: 14 }}>
      <div className="term-head">
        <span className="term-label">{draft.title}</span>
        <span className={`pill ${STATUS[draft.status][1]}`}>{STATUS[draft.status][0]}</span>
      </div>
      <div className="faint" style={{ fontSize: 12 }}>
        Version {draft.version} · {draft.agent} · requested by {draft.requested_by}
      </div>
      <div className="term-note"><b>Instructions:</b> {draft.instructions}</div>
      {draft.agent?.startsWith("baseline") && (
        <div className="callout callout-warn" style={{ marginTop: 8 }}>
          <strong>Skeleton only</strong>
          The free reader adds placeholders and the closest clause. It does not write legal wording.
        </div>
      )}
      {draft.status === "returned" && returned && (
        <div className="callout callout-warn" style={{ marginTop: 8 }}>
          <strong>Returned by {returned.actor}</strong>
          {returned.note}
        </div>
      )}

      {editing ? (
        <div>
          <label className="field">
            Title
            <input className="input" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
          </label>
          {texts.map((text, i) => (
            <label className="field" key={i}>
              Paragraph {i + 1}
              <textarea className="input" rows={Math.min(8, Math.ceil(text.length / 55) + 1)} style={{ height: "auto" }}
                value={text} maxLength={3000}
                onChange={(e) => setTexts(texts.map((t, j) => (j === i ? e.target.value : t)))} />
            </label>
          ))}
          <div className="term-actions">
            <button className="btn btn-sm btn-primary" disabled={busy}
              onClick={() => void act(() => api.editContractDraft(doc.id, draft.id, title, texts))}>Save edits</button>
            <button className="btn btn-sm btn-ghost" disabled={busy} onClick={() => setEditing(false)}>Cancel</button>
          </div>
        </div>
      ) : (
        <div className="draft-paper">
          {draft.paragraphs.map((p, i) => (
            <div key={i} className="draft-paragraph">
              <p><Wording text={p.text} /></p>
              {p.edited && p.citations.length > 0 && (
                <div className="faint" style={{ fontSize: 11 }}>Edited since drafting: recheck against the cited wording.</div>
              )}
              {p.citations.length > 0 && (
                <div className="chips">
                  {p.citations.map((c, j) => (
                    <CitationChip key={j} id={`${draft.id}-${i}-${j}`} citation={c} doc={doc} onCite={onCite} />
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {draft.open_points.length > 0 && (
        <div className="term-note">
          <b>For the lawyer to check</b>
          <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>{draft.open_points.map((x, i) => <li key={i}>{x}</li>)}</ul>
        </div>
      )}

      {draft.status === "approved" ? (
        <div className="term-actions">
          <button className="btn btn-sm" onClick={() => {
            void navigator.clipboard.writeText(plain).then(() => setCopied(true));
          }}>{copied ? "Copied" : "Copy text"}</button>
        </div>
      ) : !editing && (
        <>
          {open > 0 && <div className="term-note">{open} part{open === 1 ? "" : "s"} still to fill in before approval.</div>}
          <div className="term-actions">
            <button className="btn btn-sm" disabled={busy} onClick={() => setEditing(true)}>Edit</button>
            {draft.status === "ready" && (
              <>
                <input className="input" value={note} onChange={(e) => setNote(e.target.value)}
                  placeholder="Note (needed to return)" aria-label="Note on this draft" maxLength={1000} />
                <button className="btn btn-sm" disabled={busy || open > 0}
                  onClick={() => void act(() => api.decideContractDraft(doc.id, draft.id, "approved", note))}>Approve</button>
                <button className="btn btn-sm" disabled={busy || note.trim().length < 5}
                  onClick={() => void act(() => api.decideContractDraft(doc.id, draft.id, "returned", note))}>Return</button>
              </>
            )}
          </div>
        </>
      )}

      <div className="term-meta" style={{ display: "block" }}>
        {draft.history.map((e, i) => (
          <div key={i}>
            {ACTION[e.action]} v{e.version} by {e.actor} on {formatDate(e.at)}{e.note ? `: ${e.note}` : ""}
          </div>
        ))}
      </div>
    </div>
  );
}
