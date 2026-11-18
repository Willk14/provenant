"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { formatDate } from "@/lib/format";
import { taxApi, type Client } from "@/lib/tax";
import { usePersona } from "@/lib/usePersona";

export default function ClientsPage() {
  const persona = usePersona();
  const [clients, setClients] = useState<Client[] | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setClients(await taxApi.clients());
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    if (persona) void load();
  }, [persona, load]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    try {
      await taxApi.createClient(name);
      setName("");
      setError(null);
      await load();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Clients &amp; tax</h1>
          <p className="muted">
            Map each client&apos;s trial balance to the financial statements and to its tax treatment, then compute
            corporation tax with every adjustment traced to the accounts behind it.
          </p>
        </div>
      </div>

      <form className="dropzone" onSubmit={create}>
        <div className="dropzone-icon" aria-hidden>
          +
        </div>
        <div style={{ flex: 1 }}>
          <strong>Add a client</strong>
          <div className="muted" style={{ fontSize: 13 }}>
            Kenya corporation tax (Income Tax Act). Rules version ke-2026.3.
          </div>
        </div>
        <input
          className="input"
          placeholder="Client name, e.g. Kilima Analytics Ltd"
          value={name}
          onChange={(e) => setName(e.target.value)}
          aria-label="Client name"
          style={{ width: 300 }}
        />
        <button className="btn btn-primary" disabled={name.trim().length < 2}>
          Add client
        </button>
      </form>

      {error && <div className="error-banner">{error}</div>}

      <div className="card">
        {clients === null ? (
          <div className="empty">Loading…</div>
        ) : clients.length === 0 ? (
          <div className="empty">No clients yet. Add one above, then upload a year&apos;s trial balance.</div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Client</th>
                <th>Years</th>
                <th style={{ textAlign: "right" }}>Added</th>
              </tr>
            </thead>
            <tbody>
              {clients.map((c) => (
                <tr key={c.id}>
                  <td>
                    <Link href={`/clients/${c.id}`} style={{ fontWeight: 550 }}>
                      {c.name}
                    </Link>
                  </td>
                  <td className="muted">
                    {c.year_ends.length
                      ? c.year_ends.map((y) => `FY ${formatDate(y)}`).join(" · ")
                      : "No trial balance yet"}
                  </td>
                  <td className="muted num" style={{ textAlign: "right" }}>
                    {formatDate(c.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </main>
  );
}
