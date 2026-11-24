"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

type Agent = { href: string; label: string; hint: string; active: (path: string, hash: string) => boolean };

const on = (page: string, anchor?: string) => (path: string, hash: string) =>
  path === page && (!anchor || hash === anchor);

/** Every agent in the app, grouped by the work it does, each linking to where it runs. */
export const AGENT_GROUPS: { title: string; agents: Agent[] }[] = [
  {
    title: "Contracts",
    agents: [
      { href: "/agents#agent-reviewer", label: "Contract reviewer", hint: "Cited findings against the playbook",
        active: on("/agents", "#agent-reviewer") },
      { href: "/agents#agent-questions", label: "Contract Q&A", hint: "Answers that quote the contract",
        active: on("/agents", "#agent-questions") },
      { href: "/agents#agent-drafter", label: "Paralegal drafter", hint: "Notices, amendments and letters",
        active: on("/agents", "#agent-drafter") },
    ],
  },
  {
    title: "Practice",
    agents: [
      { href: "/matters#intake", label: "Conflict checker", hint: "Screens every party at intake",
        active: on("/matters", "#intake") },
      { href: "/matters#file-audit", label: "File auditor", hint: "KYC gaps and practising certificates",
        active: on("/matters", "#file-audit") },
      { href: "/precedents", label: "Precedent drafting", hint: "Fills the firm's locked templates",
        active: (p) => p.startsWith("/precedents") },
      { href: "/docket", label: "Docketing", hint: "Deadlines from court notices",
        active: (p) => p.startsWith("/docket") },
      { href: "/matters#matter-list", label: "Due diligence", hint: "Contract matrix on each matter",
        active: (p, h) => p.startsWith("/matters/") || (p === "/matters" && h === "#matter-list") },
    ],
  },
  {
    title: "Finance",
    agents: [
      { href: "/trust", label: "Trust reconciler", hint: "Three-way client account check",
        active: (p) => p.startsWith("/trust") },
    ],
  },
];

export function AgentRail() {
  const path = usePathname() ?? "";
  const [hash, setHash] = useState("");

  useEffect(() => {
    const read = () => setHash(window.location.hash);
    read();
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, [path]);

  // The section an agent link points at often appears only once the page's data loads: wait for it, then scroll.
  useEffect(() => {
    if (!hash) return;
    let tries = 0;
    const timer = setInterval(() => {
      const target = document.getElementById(hash.slice(1));
      if (target || ++tries > 30) {
        clearInterval(timer);
        target?.scrollIntoView({ block: "start" });
      }
    }, 100);
    return () => clearInterval(timer);
  }, [path, hash]);

  // On a phone the panel is a scrolling row: keep the current agent in view.
  useEffect(() => {
    document.querySelector(".agent-rail [aria-current='page']")?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [path, hash]);

  return (
    <aside className="agent-rail" aria-label="AI agents">
      <Link href="/agents" className="agent-rail-all" aria-current={path === "/agents" && !hash ? "page" : undefined}
        onClick={() => setHash("")}>
        All AI agents
      </Link>
      {AGENT_GROUPS.map((g) => (
        <div key={g.title} className="agent-rail-group">
          <div className="agent-rail-title">{g.title}</div>
          {g.agents.map((a) => (
            <Link key={a.href} href={a.href} title={a.hint} onClick={() => setHash(a.href.includes("#") ? a.href.slice(a.href.indexOf("#")) : "")}
              aria-current={a.active(path, hash) ? "page" : undefined}>
              <span className="agent-rail-name">{a.label}</span>
              <span className="agent-rail-hint">{a.hint}</span>
            </Link>
          ))}
        </div>
      ))}
    </aside>
  );
}
