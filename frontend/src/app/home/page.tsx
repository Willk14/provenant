"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { KIND_LABEL, todoApi, type Todo, type TodoItem } from "@/lib/todo";
import type { DocumentSummary } from "@/lib/types";
import { usePersona } from "@/lib/usePersona";

const STARTERS = [
  "What are the financial covenants and when is the first test date?",
  "Can the lender cancel on a change of control?",
  "When does the rent escalate, and by how much?",
  "Summarise the termination rights of each party.",
];

function greeting(name: string): string {
  const hour = new Date().getHours();
  const part = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
  return `${part}, ${name}`;
}

function Row({ item }: { item: TodoItem }) {
  return (
    <Link href={item.href} className={`todo-row${item.urgent ? " urgent" : ""}`}>
      <span className="todo-kind">{KIND_LABEL[item.kind]}</span>
      <span className="todo-main">
        <span className="todo-title">{item.title}</span>
        <span className="todo-detail">{item.detail}</span>
      </span>
      <span className="todo-by">
        {item.by_claude ? <span className="pill pill-manual">Prepared by Claude</span>
          : item.prepared_by ? <span className="faint">by {item.prepared_by}</span> : null}
        {!item.yours && item.why_not && <span className="faint">Not yours: {item.why_not}</span>}
      </span>
      <span className="todo-go" aria-hidden>→</span>
    </Link>
  );
}

/** One box to start from: ask Claude about a contract, or add a document. The work waiting for you sits below. */
function Composer({ contracts }: { contracts: DocumentSummary[] }) {
  const router = useRouter();
  const [contract, setContract] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [menu, setMenu] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  const kind = useRef<"lease" | "credit_agreement">("lease");
  const box = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    setContract((c) => c || contracts[0]?.id || "");
  }, [contracts]);

  async function ask() {
    if (!message.trim() || !contract) return;
    setBusy(true);
    setError(null);
    try {
      await api.startContractChat(contract, message.trim());
      router.push(`/documents/${contract}?tab=ask`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  async function upload(f: File) {
    setBusy(true);
    setError(null);
    try {
      const made = await api.upload(f, undefined, kind.current);
      router.push(`/documents/${made.id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="composer-wrap">
      <form className="composer" onSubmit={(e) => { e.preventDefault(); void ask(); }}>
        <textarea ref={box} rows={2} value={message} placeholder={contracts.length
          ? "Ask anything about a contract…" : "Add a contract to start asking questions about it…"}
          aria-label="Ask about a contract" disabled={busy || !contracts.length}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void ask();
            }
          }} />
        <div className="composer-bar">
          <div className="composer-left">
            <div className="composer-attach">
              <button type="button" className="icon-btn" aria-label="Add a document" aria-expanded={menu}
                disabled={busy} onClick={() => setMenu(!menu)}>＋</button>
              {menu && (
                <div className="composer-menu" role="menu">
                  <button type="button" role="menuitem" onClick={() => { kind.current = "lease"; setMenu(false); file.current?.click(); }}>
                    Upload a lease
                  </button>
                  <button type="button" role="menuitem" onClick={() => { kind.current = "credit_agreement"; setMenu(false); file.current?.click(); }}>
                    Upload a facility agreement
                  </button>
                  <Link role="menuitem" href="/documents">More document types…</Link>
                </div>
              )}
              <input ref={file} type="file" accept="application/pdf,.pdf" hidden
                onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ""; }} />
            </div>
            {contracts.length > 0 && (
              <select className="composer-context" aria-label="Contract to ask about" value={contract}
                disabled={busy} onChange={(e) => setContract(e.target.value)}>
                {contracts.map((c) => <option key={c.id} value={c.id}>{c.filename}</option>)}
              </select>
            )}
          </div>
          <button className="composer-send" aria-label="Send" disabled={busy || !message.trim() || !contract}>
            {busy ? "…" : "↑"}
          </button>
        </div>
      </form>
      {error && <div className="error-banner">{error}</div>}
      {contracts.length > 0 && (
        <div className="composer-starters">
          {STARTERS.map((s) => (
            <button key={s} type="button" onClick={() => { setMessage(s); box.current?.focus(); }}>{s}</button>
          ))}
        </div>
      )}
      <p className="composer-note">Answers quote the contract wording they rely on. Check them before you rely on them.</p>
    </div>
  );
}

export default function HomePage() {
  const persona = usePersona();
  const [todo, setTodo] = useState<Todo | null>(null);
  const [contracts, setContracts] = useState<DocumentSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!persona) return;
    setTodo(null);
    todoApi.todo().then(setTodo).catch((e: Error) => setError(e.message));
    api.documents().then((d) => setContracts(d.filter((x) => x.kind !== "amendment" && x.status !== "failed")))
      .catch(() => setContracts([]));
  }, [persona]);

  const yours = todo?.items.filter((x) => x.yours) ?? [];
  const others = todo?.items.filter((x) => !x.yours) ?? [];
  return (
    <main className="home">
      <section className="home-hero">
        <h1>{persona ? greeting(persona.name.split(" ")[0]) : "Welcome"}</h1>
        <p className="muted">What would you like to work on?</p>
        <Composer contracts={contracts} />
      </section>

      {error && <div className="error-banner">{error}</div>}
      {todo && (
        <section className="home-list">
          <h2>For you {yours.length > 0 && <span className="faint">{yours.length}</span>}</h2>
          <div className="todo-list">
            {yours.length === 0 ? <div className="empty">Nothing is waiting for you.</div>
              : yours.map((x, i) => <Row key={`${x.kind}-${x.href}-${i}`} item={x} />)}
          </div>
          {others.length > 0 && (
            <>
              <h2>Waiting on someone else <span className="faint">{others.length}</span></h2>
              <div className="todo-list muted-list">
                {others.map((x, i) => <Row key={`${x.kind}-${x.href}-${i}`} item={x} />)}
              </div>
            </>
          )}
        </section>
      )}
    </main>
  );
}
