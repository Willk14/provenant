"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { memoApi, type Memo, type MemoBlock } from "@/lib/tax";

const TABLE_LABELS: Record<string, string> = {
  computation: "Computation table",
  deferred: "Deferred tax table",
  checks: "Compliance checks table",
};

export function MemoTab({ clientId, yearEnd }: { clientId: string; yearEnd: string }) {
  const [memo, setMemo] = useState<Memo | null>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const editor = useRef<HTMLTextAreaElement>(null);

  const load = useCallback(async () => {
    try {
      const m = await memoApi.get(clientId, yearEnd);
      setMemo(m);
      setDraft(m.body);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [clientId, yearEnd]);

  useEffect(() => {
    void load();
  }, [load]);

  async function save(body: string) {
    setBusy(true);
    setError(null);
    try {
      const m = await memoApi.save(clientId, yearEnd, body);
      setMemo(m);
      setDraft(m.body);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function insert(token: string) {
    const el = editor.current;
    const at = el?.selectionStart ?? draft.length;
    setDraft(draft.slice(0, at) + token + draft.slice(el?.selectionEnd ?? at));
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(at + token.length, at + token.length);
    });
  }

  if (!memo) return error ? <div className="error-banner">{error}</div> : <div className="empty">Loading…</div>;
  const dirty = draft !== memo.body;

  return (
    <div className="memo-layout">
      <section className="card record-card memo-editor">
        <div className="record-head">
          <div>
            <h3>Board tax memo</h3>
            <p className="term-note">
              Write the memo around fields in double braces. Every figure comes from the computation, so changing an
              adjustment updates the memo. Type no amounts: use a field.
            </p>
          </div>
        </div>
        <div className="term-actions">
          <select className="input" value="" aria-label="Insert a field" disabled={!memo.fields.length}
            onChange={(e) => e.target.value && insert(`{{${e.target.value}}}`)}>
            <option value="">Insert a field…</option>
            {memo.fields.map((f) => (
              <option key={f.key} value={f.key}>{f.label}: {f.text.length > 40 ? `${f.text.slice(0, 40)}…` : f.text}</option>
            ))}
          </select>
          <select className="input" value="" aria-label="Insert a table"
            onChange={(e) => e.target.value && insert(`\n{{table:${e.target.value}}}\n`)}>
            <option value="">Insert a table…</option>
            {memo.tables.map((t) => <option key={t} value={t}>{TABLE_LABELS[t] ?? t}</option>)}
          </select>
        </div>
        <textarea ref={editor} className="input memo-text" value={draft} spellCheck
          aria-label="Memo text" onChange={(e) => setDraft(e.target.value)} />
        <div className="term-actions">
          <button className="btn btn-primary btn-sm" type="button" disabled={busy || !dirty} onClick={() => save(draft)}>
            {busy ? "Saving…" : "Save memo"}
          </button>
          {dirty && (
            <button className="btn btn-sm" type="button" onClick={() => setDraft(memo.body)}>Discard changes</button>
          )}
          {!memo.is_default && (
            <button className="btn btn-sm btn-ghost" type="button" disabled={busy} onClick={() => save("")}>
              Reset to the standard memo
            </button>
          )}
          <span className="faint" style={{ fontSize: 12 }}>
            {memo.is_default ? "Standard memo" : `Saved by ${memo.updated_by}`}
          </span>
        </div>
        {error && <div className="error-banner">{error}</div>}
      </section>

      <section className="card record-card">
        <div className="record-head">
          <div className="term-label">Preview{dirty && " (of the saved memo: save to update)"}</div>
          <button className="btn btn-sm" type="button" disabled={!!memo.blocked} onClick={() => window.print()}>
            Print or save as PDF
          </button>
        </div>
        {memo.warnings.map((w) => (
          <div key={w} className="callout callout-warn" style={{ fontSize: 13, padding: "6px 10px" }}>{w}</div>
        ))}
        {memo.blocked ? (
          <div className="callout callout-warn">
            <strong>The memo is waiting on the computation</strong>
            {memo.blocked}.
          </div>
        ) : (
          <article className="memo-print">
            {memo.blocks.map((b, i) => <BlockView key={i} block={b} sources={memo.fields} />)}
          </article>
        )}
      </section>
    </div>
  );
}

function Segments({ block, sources }: { block: MemoBlock; sources: Memo["fields"] }) {
  return (
    <>
      {block.segments.map((s, i) =>
        s.field ? (
          <span key={i} className="memo-field" title={`${sources.find((f) => f.key === s.field)?.source ?? s.field}`}>
            {s.text}
          </span>
        ) : (
          <span key={i}>{s.text}</span>
        ),
      )}
    </>
  );
}

function BlockView({ block, sources }: { block: MemoBlock; sources: Memo["fields"] }) {
  if (block.kind === "heading1") return <h2 className="memo-h1"><Segments block={block} sources={sources} /></h2>;
  if (block.kind === "heading2") return <h3 className="memo-h2"><Segments block={block} sources={sources} /></h3>;
  if (block.kind === "bullet") return <p className="memo-bullet">• <Segments block={block} sources={sources} /></p>;
  if (block.kind === "table") {
    return (
      <table className="workings-table comp-table memo-table">
        <tbody>
          {block.rows.map(([label, value, total], i) => (
            <tr key={i} data-total={total || undefined}>
              <td>{label}</td>
              <td className={block.table === "checks" ? undefined : "num"}>{value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }
  return <p className="memo-p"><Segments block={block} sources={sources} /></p>;
}
