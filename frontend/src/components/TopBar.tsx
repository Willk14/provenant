"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { AUTH_MODE, supabase } from "@/lib/auth";
import { PERSONAS, setPersona } from "@/lib/identity";
import { todoApi } from "@/lib/todo";
import { usePersona } from "@/lib/usePersona";

// Matters, clients and tax, the trust account and the team are left out of the bar; their pages are still
// reached from the items on To review.
const LINKS = [
  { href: "/home", label: "To review", also: ["/review", "/matters", "/docket", "/precedents", "/trust", "/clients", "/team"] },
  { href: "/documents", label: "Documents", also: [] },
];

export function TopBar() {
  const path = usePathname() ?? "";
  const router = useRouter();
  const persona = usePersona();
  const [count, setCount] = useState(0);

  // How many items wait for this person; refreshed as they move around the app.
  useEffect(() => {
    if (!persona) return;
    todoApi.todo().then((t) => setCount(t.yours)).catch(() => setCount(0));
  }, [persona, path]);

  return (
    <header className="topbar">
      <Link href="/home" className="brand">
        <span className="brand-mark" aria-hidden>
          P
        </span>
        Provenant
      </Link>
      <nav className="nav">
        {LINKS.map((l) => (
          <Link key={l.href} href={l.href}
            aria-current={[l.href, ...l.also].some((h) => path.startsWith(h)) ? "page" : undefined}>
            {l.label}
            {l.href === "/home" && count > 0 && <span className="nav-count">{count}</span>}
          </Link>
        ))}
      </nav>
      {AUTH_MODE === "supabase" ? (
        <div className="persona">
          <span className="account" title={persona?.roles.join(", ")}>
            <span className="who">{persona?.name ?? "…"}</span>
            <span className="role">{persona?.roles.join(", ") || "no role yet"}</span>
          </span>
          <button type="button" className="btn btn-sm" onClick={async () => {
            await supabase()?.auth.signOut();
            router.replace("/login");
          }}>
            Sign out
          </button>
        </div>
      ) : (
        <div className="persona">
          <span className="dev-badge" title="Identity comes from request headers. Local development only.">
            DEV
          </span>
          <span className="persona-label">Acting as</span>
          <div className="persona-switch" role="group" aria-label="Acting as">
            {PERSONAS.map((p) => (
              <button
                key={p.key}
                type="button"
                aria-pressed={persona?.key === p.key}
                title={p.blurb}
                onClick={() => setPersona(p.key)}
              >
                <span className="who">{p.name.split(" ")[0]}</span>
                <span className="role">{p.roles.join(", ")}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </header>
  );
}
