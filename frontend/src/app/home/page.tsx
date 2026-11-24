"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { KIND_LABEL, todoApi, type Todo, type TodoItem } from "@/lib/todo";
import { usePersona } from "@/lib/usePersona";

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

export default function HomePage() {
  const persona = usePersona();
  const [todo, setTodo] = useState<Todo | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!persona) return;
    setTodo(null);
    todoApi.todo().then(setTodo).catch((e: Error) => setError(e.message));
  }, [persona]);

  const yours = todo?.items.filter((x) => x.yours) ?? [];
  const others = todo?.items.filter((x) => !x.yours) ?? [];
  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>To review</h1>
          <p className="muted">
            Provenant prepares; people decide. Everything waiting for a decision, with where it came from and who
            prepared it. Nobody decides what they prepared themselves.
          </p>
        </div>
      </div>
      {error && <div className="error-banner">{error}</div>}
      {todo === null ? <div className="card"><div className="empty">Loading…</div></div> : (
        <>
          <section className="todo-section">
            <h2>For you <span className="faint">{yours.length}</span></h2>
            <div className="card todo-list">
              {yours.length === 0 ? <div className="empty">Nothing is waiting for you.</div>
                : yours.map((x, i) => <Row key={`${x.kind}-${x.href}-${i}`} item={x} />)}
            </div>
          </section>
          {others.length > 0 && (
            <section className="todo-section">
              <h2>Waiting on someone else <span className="faint">{others.length}</span></h2>
              <div className="card todo-list muted-list">
                {others.map((x, i) => <Row key={`${x.kind}-${x.href}-${i}`} item={x} />)}
              </div>
            </section>
          )}
        </>
      )}
    </main>
  );
}
