"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

import { AUTH_MODE, supabase } from "@/lib/auth";
import { PERSONAS, setPersona } from "@/lib/identity";
import { usePersona } from "@/lib/usePersona";

const LINKS = [
  { href: "/documents", label: "Documents" },
  { href: "/clients", label: "Clients & tax" },
  { href: "/review", label: "Review queue" },
  { href: "/matters", label: "Matters" },
  { href: "/trust", label: "Trust account" },
  { href: "/agents", label: "AI agents" },
  { href: "/team", label: "Team" },
];

export function TopBar() {
  const path = usePathname() ?? "";
  const router = useRouter();
  const persona = usePersona();

  return (
    <header className="topbar">
      <Link href="/documents" className="brand">
        <span className="brand-mark" aria-hidden>
          P
        </span>
        Provenant
      </Link>
      <nav className="nav">
        {LINKS.map((l) => (
          <Link key={l.href} href={l.href} aria-current={path.startsWith(l.href) ? "page" : undefined}>
            {l.label}
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
