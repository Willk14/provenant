"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { formatDate } from "@/lib/format";
import { phase2Api, type Precedent } from "@/lib/practice2";
import { usePersona } from "@/lib/usePersona";

const KINDS = ["engagement_letter", "nda", "lease", "employment", "pleading", "letter", "other"];
const EXAMPLE = `ENGAGEMENT LETTER

To {{client_name}}, matter {{matter_reference}}.

We will act for you in {{matter_title}}. Our fee is {{fee}}.

[[optional:retainer]]A retainer of {{retainer}} is payable before work starts.[[/optional]]

Yours faithfully, {{responsible}}`;

export default function PrecedentsPage() {
  const persona = usePersona();
  const [list, setList] = useState<Precedent[]>([]);
  const [editing, setEditing] = useState<Precedent | null>(null);
  const [name, setName] = useState("");
  const [kind, setKind] = useState("engagement_letter");
  const [body, setBody] = useState(EXAMPLE);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setList(await phase2Api.precedents());
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    if (persona) void load();
  }, [persona, load]);

  const partner = !!persona?.roles.includes("partner");
  return (
    <main className="page">
      <Link href="/matters" className="link">← Matters</Link>
      <div className="page-head">
        <div>
          <h1>Precedents</h1>
          <p className="muted">
            The firm&apos;s locked templates. Drafts are filled from a matter&apos;s facts, never generated, and any change
            from the precedent is shown to a partner before approval. Use {"{{field}}"} for facts and
            {" [[optional:name]]…[[/optional]]"} for clauses chosen per matter.
          </p>
        </div>
      </div>
      {error && <div className="error-banner">{error}</div>}
      {partner && (
        <form className="card practice-form" onSubmit={(e) => {
          e.preventDefault();
          setBusy(true);
          setError(null);
          const action = editing ? phase2Api.newVersion(editing.id, body) : phase2Api.createPrecedent(name, kind, body);
          void action.then(async () => { setEditing(null); setName(""); await load(); })
            .catch((err: Error) => setError(err.message)).finally(() => setBusy(false));
        }}>
          <strong>{editing ? `Lock version ${editing.version + 1} of ${editing.name}` : "Lock a new precedent"}</strong>
          {!editing && (
            <div className="practice-row">
              <input className="input grow" required placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} aria-label="Precedent name" />
              <select className="input" value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Kind">
                {KINDS.map((k) => <option key={k} value={k}>{k.replace(/_/g, " ")}</option>)}
              </select>
            </div>
          )}
          <textarea className="input mono" rows={12} style={{ height: "auto" }} value={body} onChange={(e) => setBody(e.target.value)} aria-label="Precedent text" />
          <div className="practice-row">
            <button className="btn btn-primary btn-sm" disabled={busy}>{editing ? "Lock new version" : "Lock precedent"}</button>
            {editing && <button type="button" className="btn btn-sm btn-ghost" onClick={() => { setEditing(null); setBody(EXAMPLE); }}>Cancel</button>}
          </div>
        </form>
      )}
      <div className="card">
        {list.length === 0 ? <div className="empty">No precedents yet.{partner ? " Lock the first one above." : " A partner adds them."}</div> : (
          <table className="table">
            <thead><tr><th>Precedent</th><th>Fields</th><th>Version</th><th /></tr></thead>
            <tbody>{list.map((p) => (
              <tr key={p.id}>
                <td><b>{p.name}</b><div className="faint" style={{ fontSize: 12 }}>{p.kind.replace(/_/g, " ")}</div></td>
                <td className="muted" style={{ fontSize: 12 }}>{p.fields.join(", ")}{p.options.length ? ` · optional: ${p.options.join(", ")}` : ""}</td>
                <td className="muted">v{p.version} by {p.locked_by}, {formatDate(p.locked_at)}</td>
                <td>{partner && <button className="btn btn-sm" onClick={() => { setEditing(p); setBody(p.body); }}>New version</button>}</td>
              </tr>))}</tbody>
          </table>
        )}
      </div>
    </main>
  );
}
