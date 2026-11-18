"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { PERSONAS, setPersona } from "@/lib/identity";
import { usePersona } from "@/lib/usePersona";

export function TopBar() {
  const path = usePathname();
  const persona = usePersona();

  return (
    <header className="topbar">
      <Link href="/" className="brand">
        <span className="brand-mark" aria-hidden>
          P
        </span>
        Provenant
      </Link>
      <nav className="nav">
        <Link href="/" aria-current={path === "/" || path.startsWith("/documents") ? "page" : undefined}>
          Documents
        </Link>
        <Link href="/clients" aria-current={path.startsWith("/clients") ? "page" : undefined}>
          Clients &amp; tax
        </Link>
        <Link href="/review" aria-current={path === "/review" ? "page" : undefined}>
          Review queue
        </Link>
      </nav>
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
    </header>
  );
}
