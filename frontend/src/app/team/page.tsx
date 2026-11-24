"use client";

import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { Me, Member } from "@/lib/types";
import { usePersona } from "@/lib/usePersona";

const ROLES = ["preparer", "reviewer", "partner"] as const;

export default function TeamPage() {
  const persona = usePersona();
  const [me, setMe] = useState<Me | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [email, setEmail] = useState("");
  const [roles, setRoles] = useState<string[]>(["preparer"]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [who, list] = await Promise.all([api.me(), api.members()]);
      setMe(who);
      setMembers(list);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    if (persona) void load();
  }, [persona, load]);

  async function act(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const partner = !!me?.roles.includes("partner");
  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Team</h1>
          <p className="muted">Who works in this firm&apos;s workspace. Partners invite people and choose their roles.</p>
        </div>
      </div>
      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="callout callout-ok">{notice}</div>}

      {me && (
        <section className="card team-me">
          <div>
            <strong>{me.email || me.user}</strong>
            <div className="faint" style={{ fontSize: 12 }}>{me.roles.join(", ")}</div>
          </div>
          <label className="team-toggle">
            <input type="checkbox" checked={me.notify} disabled={busy || !me.email}
              onChange={(e) => void act(async () => {
                setMe(await api.setNotifications(e.target.checked));
              })} />
            Email me when work is waiting for me
          </label>
        </section>
      )}

      {partner && (
        <form className="card team-invite" onSubmit={(e) => {
          e.preventDefault();
          void act(async () => {
            const result = await api.inviteMember(email, roles);
            setEmail("");
            setNotice(result.email_sent
              ? `Invitation sent to ${result.member.email}.`
              : `${result.member.email} was added, but no email was sent: sign-in is not connected yet.`);
          });
        }}>
          <strong>Invite someone</strong>
          <div className="team-invite-row">
            <input className="input" type="email" required placeholder="name@yourfirm.co.ke" aria-label="Email to invite"
              value={email} onChange={(e) => setEmail(e.target.value)} />
            <div className="segmented" role="group" aria-label="Roles">
              {ROLES.map((r) => (
                <button key={r} type="button" aria-pressed={roles.includes(r)}
                  onClick={() => setRoles(roles.includes(r) ? roles.filter((x) => x !== r) : [...roles, r])}>
                  {r}
                </button>
              ))}
            </div>
            <button className="btn btn-primary" disabled={busy || !email || roles.length === 0}>Send invitation</button>
          </div>
          <span className="faint" style={{ fontSize: 12 }}>
            Maker-checker still applies: nobody can approve work they uploaded or entered.
          </span>
        </form>
      )}

      <div className="card">
        {members.length === 0 ? (
          <div className="empty">Nobody is in the directory yet. People appear here when they sign in or are invited.</div>
        ) : (
          <table className="table">
            <thead><tr><th>Email</th><th>Roles</th><th>Status</th><th style={{ textAlign: "right" }}>Last seen</th></tr></thead>
            <tbody>
              {members.map((m) => (
                <tr key={m.id}>
                  <td>{m.email}</td>
                  <td className="muted">{m.roles.join(", ")}</td>
                  <td><span className={`pill ${m.status === "active" ? "pill-ok" : "pill-warn"}`}>{m.status === "active" ? "Active" : "Invited"}</span></td>
                  <td className="muted num" style={{ textAlign: "right" }}>{m.last_seen_at ? formatDate(m.last_seen_at) : "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </main>
  );
}
