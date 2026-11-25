"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { AUTH_MODE, supabase } from "@/lib/auth";
import { PERSONAS, setPersona } from "@/lib/identity";
import { practiceApi, type MatterSummary } from "@/lib/practice";
import { todoApi } from "@/lib/todo";
import type { DocumentSummary } from "@/lib/types";
import { usePersona } from "@/lib/usePersona";

/** The app's frame on the left: start something new, what waits for you, and what you worked on recently. */
export function Sidebar() {
  const path = usePathname() ?? "";
  const router = useRouter();
  const persona = usePersona();
  const [open, setOpen] = useState(false);
  const [count, setCount] = useState(0);
  const [docs, setDocs] = useState<DocumentSummary[]>([]);
  const [matters, setMatters] = useState<MatterSummary[]>([]);

  useEffect(() => setOpen(false), [path]);

  useEffect(() => {
    if (!persona) return;
    todoApi.todo().then((t) => setCount(t.yours)).catch(() => setCount(0));
    api.documents().then((d) => setDocs(d.filter((x) => x.kind !== "amendment").slice(0, 6))).catch(() => setDocs([]));
    practiceApi.matters().then((m) => setMatters(m.slice(0, 5))).catch(() => setMatters([]));
  }, [persona, path]);

  const here = (href: string) => path === href || path.startsWith(`${href}/`);
  return (
    <>
      <header className="mobile-bar">
        <button type="button" className="icon-btn" aria-label="Open menu" aria-expanded={open} onClick={() => setOpen(true)}>
          <span aria-hidden>☰</span>
        </button>
        <Link href="/home" className="brand"><span className="brand-mark" aria-hidden>P</span>Provenant</Link>
        <Link href="/home" className="icon-btn" aria-label="New">＋</Link>
      </header>
      {open && <div className="sidebar-scrim" onClick={() => setOpen(false)} aria-hidden />}

      <aside className={`sidebar${open ? " open" : ""}`} aria-label="Main">
        <div className="sidebar-top">
          <Link href="/home" className="brand"><span className="brand-mark" aria-hidden>P</span>Provenant</Link>
          <button type="button" className="icon-btn sidebar-close" aria-label="Close menu" onClick={() => setOpen(false)}>✕</button>
        </div>

        <Link href="/home" className="sidebar-new">
          <span aria-hidden>＋</span> New
        </Link>

        <nav className="sidebar-nav">
          <Link href="/home" aria-current={here("/home") ? "page" : undefined}>
            <span>To review</span>
            {count > 0 && <span className="sidebar-count">{count}</span>}
          </Link>
          <Link href="/documents" aria-current={here("/documents") ? "page" : undefined}>
            <span>Documents</span>
          </Link>
        </nav>

        <div className="sidebar-recent">
          {docs.length > 0 && <div className="sidebar-heading">Recent documents</div>}
          {docs.map((d) => (
            <Link key={d.id} href={`/documents/${d.id}`} aria-current={here(`/documents/${d.id}`) ? "page" : undefined}
              title={d.filename}>
              {d.filename.replace(/\.pdf$/i, "")}
            </Link>
          ))}
          {matters.length > 0 && <div className="sidebar-heading">Recent matters</div>}
          {matters.map((m) => (
            <Link key={m.id} href={`/matters/${m.id}`} aria-current={here(`/matters/${m.id}`) ? "page" : undefined}
              title={`${m.reference} · ${m.title}`}>
              {m.title}
            </Link>
          ))}
        </div>

        <div className="sidebar-account">
          {AUTH_MODE === "supabase" ? (
            <>
              <div className="sidebar-person">
                <span className="avatar" aria-hidden>{(persona?.name ?? "?").slice(0, 1).toUpperCase()}</span>
                <span><b>{persona?.name ?? "…"}</b><span className="faint">{persona?.roles.join(", ") || "no role yet"}</span></span>
              </div>
              <button type="button" className="btn btn-sm btn-ghost" onClick={async () => {
                await supabase()?.auth.signOut();
                router.replace("/login");
              }}>Sign out</button>
            </>
          ) : (
            <>
              <div className="sidebar-heading">Acting as <span className="dev-badge" title="Identity comes from request headers. Local development only.">DEV</span></div>
              <div className="sidebar-people" role="group" aria-label="Acting as">
                {PERSONAS.map((p) => (
                  <button key={p.key} type="button" aria-pressed={persona?.key === p.key} title={p.blurb}
                    onClick={() => setPersona(p.key)}>
                    <span className="avatar" aria-hidden>{p.name.slice(0, 1)}</span>
                    <span><b>{p.name.split(" ")[0]}</b><span className="faint">{p.roles.join(", ")}</span></span>
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      </aside>
    </>
  );
}
