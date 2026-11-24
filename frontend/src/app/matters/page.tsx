"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { formatDate } from "@/lib/format";
import { practiceApi, STATUS_PILL, STATUS_WORD, type Audit, type MatterSummary, type PartyIn } from "@/lib/practice";
import { usePersona } from "@/lib/usePersona";

const ROLES: { value: PartyIn["role"]; label: string }[] = [
  { value: "client", label: "Client" },
  { value: "counterparty", label: "Other side" },
  { value: "related", label: "Related party" },
  { value: "beneficial_owner", label: "Beneficial owner" },
];
const blank = (role: PartyIn["role"]): PartyIn => ({ name: "", role, identifier: "" });

export default function MattersPage() {
  const persona = usePersona();
  const router = useRouter();
  const [matters, setMatters] = useState<MatterSummary[] | null>(null);
  const [audit, setAudit] = useState<Audit | null>(null);
  const [lists, setLists] = useState<{ list_name: string; entries: number; uploaded_at: string }[]>([]);
  const [title, setTitle] = useState("");
  const [type, setType] = useState("corporate");
  const [kind, setKind] = useState<"person" | "organisation">("organisation");
  const [parties, setParties] = useState<PartyIn[]>([blank("client"), blank("counterparty")]);
  const [listName, setListName] = useState("");
  const [cert, setCert] = useState({ holder: "", number: "", expires: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [m, a, w] = await Promise.all([practiceApi.matters(), practiceApi.audit(), practiceApi.watchlists()]);
      setMatters(m);
      setAudit(a);
      setLists(w);
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

  const partner = !!persona?.roles.includes("partner");
  const issues = (audit?.matters.length ?? 0) + (audit?.certificates.length ?? 0);
  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Matters</h1>
          <p className="muted">
            Open a matter, and the conflict agent checks every party against your matters and screening lists. Someone
            other than the person who opened it decides. The file auditor watches each open matter&apos;s KYC.
          </p>
        </div>
        <div className="practice-row">
          <Link className="btn btn-sm" href="/docket">Docket</Link>
          <Link className="btn btn-sm" href="/precedents">Precedents</Link>
        </div>
      </div>
      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="callout callout-ok">{notice}</div>}

      <form className="card practice-form" onSubmit={(e) => {
        e.preventDefault();
        void act(async () => {
          const made = await practiceApi.openMatter({ title, matter_type: type, client_kind: kind,
            parties: parties.filter((p) => p.name.trim()) });
          router.push(`/matters/${made.id}`);
        });
      }}>
        <strong>New matter intake</strong>
        <div className="practice-row">
          <label className="field grow">Title
            <input className="input" required value={title} onChange={(e) => setTitle(e.target.value)}
              placeholder="Kilima Analytics acquisition of Baobab Logistics" />
          </label>
          <label className="field">Type
            <select className="input" value={type} onChange={(e) => setType(e.target.value)}>
              {["corporate", "litigation", "property", "employment", "banking", "tax", "general"].map((t) =>
                <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
          <label className="field">Client is
            <select className="input" value={kind} onChange={(e) => setKind(e.target.value as "person" | "organisation")}>
              <option value="organisation">an organisation</option>
              <option value="person">a person</option>
            </select>
          </label>
        </div>
        {parties.map((p, i) => (
          <div className="practice-row" key={i}>
            <select className="input" aria-label={`Party ${i + 1} role`} value={p.role}
              onChange={(e) => setParties(parties.map((x, j) => j === i ? { ...x, role: e.target.value as PartyIn["role"] } : x))}>
              {ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
            </select>
            <input className="input grow" aria-label={`Party ${i + 1} name`} placeholder="Full name" value={p.name}
              onChange={(e) => setParties(parties.map((x, j) => j === i ? { ...x, name: e.target.value } : x))} />
            <input className="input" aria-label={`Party ${i + 1} ID`} placeholder="ID or registration no. (optional)"
              value={p.identifier}
              onChange={(e) => setParties(parties.map((x, j) => j === i ? { ...x, identifier: e.target.value } : x))} />
          </div>
        ))}
        <div className="practice-row">
          <button type="button" className="btn btn-sm btn-ghost" onClick={() => setParties([...parties, blank("related")])}>
            Add party
          </button>
          <button className="btn btn-primary" disabled={busy || title.trim().length < 3 || !parties.some((p) => p.role === "client" && p.name.trim())}>
            Open matter and check conflicts
          </button>
        </div>
      </form>

      {audit && (
        <section className="card practice-audit">
          <div className="practice-row space">
            <strong>File audit</strong>
            <span className={`pill ${issues ? "pill-warn" : "pill-ok"}`}>{issues ? `${issues} to fix` : "Nothing missing"}</span>
          </div>
          <span className="faint" style={{ fontSize: 12 }}>
            Computed now for every open matter, against {audit.requirements_version}.
          </span>
          {audit.matters.map((m) => (
            <div key={m.id} className="practice-gap">
              <Link className="link" href={`/matters/${m.id}`}>{m.reference} · {m.title}</Link>
              <ul>{m.gaps.map((g) => <li key={g.item}><b>{g.problem}:</b> {g.detail}</li>)}</ul>
            </div>
          ))}
          {audit.certificates.length > 0 && (
            <div className="practice-gap">
              <b>Practising certificates</b>
              <ul>{audit.certificates.map((g) => <li key={g.label}><b>{g.problem}:</b> {g.detail}</li>)}</ul>
            </div>
          )}
        </section>
      )}

      <div className="card">
        {matters === null ? <div className="empty">Loading…</div> : matters.length === 0 ? (
          <div className="empty">No matters yet. Open the first one above.</div>
        ) : (
          <table className="table">
            <thead><tr><th>Matter</th><th>Status</th><th>Conflicts</th><th>File gaps</th><th style={{ textAlign: "right" }}>Opened</th></tr></thead>
            <tbody>
              {matters.map((m) => (
                <tr key={m.id}>
                  <td><Link href={`/matters/${m.id}`} style={{ fontWeight: 550 }}>{m.reference} · {m.title}</Link>
                    <div className="faint" style={{ fontSize: 12 }}>{m.matter_type} · by {m.created_by}</div></td>
                  <td><span className={`pill ${STATUS_PILL[m.status]}`}>{STATUS_WORD[m.status]}</span></td>
                  <td className="num">{m.possible_conflicts}</td>
                  <td className="num">{m.status === "open" ? m.gaps : "–"}</td>
                  <td className="muted num" style={{ textAlign: "right" }}>{formatDate(m.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {partner && (
        <div className="practice-columns">
          <form className="card practice-form" onSubmit={(e) => {
            e.preventDefault();
            const file = (e.currentTarget.elements.namedItem("list") as HTMLInputElement).files?.[0];
            if (!file) return;
            void act(async () => {
              const done = await practiceApi.uploadWatchlist(listName, file);
              setNotice(`${done.entries} names loaded into ${listName}.`);
            });
          }}>
            <strong>Screening lists</strong>
            <span className="faint" style={{ fontSize: 12 }}>
              Sanctions and PEP lists as CSV with a name column (and optionally identifier, reference). A new upload
              replaces the list of the same name.
            </span>
            {lists.map((l) => <div key={l.list_name} className="faint" style={{ fontSize: 13 }}>{l.list_name}: {l.entries} names, {formatDate(l.uploaded_at)}</div>)}
            <div className="practice-row">
              <input className="input grow" required placeholder="List name, e.g. UN consolidated" value={listName}
                onChange={(e) => setListName(e.target.value)} aria-label="List name" />
              <input name="list" type="file" accept=".csv,text/csv" required aria-label="Screening list CSV" />
              <button className="btn btn-sm" disabled={busy}>Upload</button>
            </div>
          </form>
          <form className="card practice-form" onSubmit={(e) => {
            e.preventDefault();
            void act(async () => {
              await practiceApi.addCertificate(cert.holder, cert.number, cert.expires);
              setCert({ holder: "", number: "", expires: "" });
            });
          }}>
            <strong>Practising certificates</strong>
            <span className="faint" style={{ fontSize: 12 }}>Reviewers and partners are treated as advocates who need a current certificate.</span>
            <div className="practice-row">
              <input className="input grow" required type="email" placeholder="advocate@firm" value={cert.holder}
                onChange={(e) => setCert({ ...cert, holder: e.target.value })} aria-label="Advocate email" />
              <input className="input" required placeholder="Certificate no." value={cert.number}
                onChange={(e) => setCert({ ...cert, number: e.target.value })} aria-label="Certificate number" />
              <input className="input" required type="date" value={cert.expires}
                onChange={(e) => setCert({ ...cert, expires: e.target.value })} aria-label="Expiry date" />
              <button className="btn btn-sm" disabled={busy}>Record</button>
            </div>
          </form>
        </div>
      )}
    </main>
  );
}
