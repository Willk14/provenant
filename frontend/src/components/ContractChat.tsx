"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { AiAgents, ContractChat as Chat, LegalCitation } from "@/lib/types";

const citationHref = (c: LegalCitation) =>
  `/documents/${c.document_id}?tab=review&page=${c.page}&bbox=${c.bbox.join(",")}`;

export function ContractChat({ contractId, filename, mode }: {
  contractId: string;
  filename: string;
  mode: AiAgents["engine"]["mode"];
}) {
  const [chats, setChats] = useState<Chat[]>([]);
  const [chatId, setChatId] = useState<string | null>(null);
  const [fresh, setFresh] = useState(false);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const end = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      setChats(await api.contractChats(contractId));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [contractId]);

  useEffect(() => {
    setChatId(null);
    setFresh(false);
    setChats([]);
    void load();
  }, [load]);

  const chat = fresh ? undefined : chats.find((c) => c.id === chatId) ?? chats[0];
  const working = chats.some((c) => c.status === "queued" || c.status === "running");
  useEffect(() => {
    if (!working) return;
    const timer = setInterval(load, 1200);
    return () => clearInterval(timer);
  }, [working, load]);

  const count = chat?.messages.length ?? 0;
  const thinking = chat?.status === "queued" || chat?.status === "running";
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "nearest" });
  }, [count, thinking]);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const saved = chat
        ? await api.sendChatMessage(contractId, chat.id, message)
        : await api.startContractChat(contractId, message);
      setChatId(saved.id);
      setFresh(false);
      setMessage("");
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card chat" aria-label="Contract chat">
      <div className="chat-head">
        <div>
          <h2>Ask Claude about this contract</h2>
          <div className="faint" style={{ fontSize: 12 }}>{filename}</div>
        </div>
        <div className="chat-tools">
          {chats.length > 1 && !fresh && (
            <select className="input" aria-label="Earlier chats" value={chat?.id}
              onChange={(e) => setChatId(e.target.value)}>
              {chats.map((c) => <option key={c.id} value={c.id}>{c.title.slice(0, 44)} · {formatDate(c.created_at)}</option>)}
            </select>
          )}
          {chats.length > 0 && !fresh && (
            <button type="button" className="btn btn-sm" disabled={working} onClick={() => setFresh(true)}>New chat</button>
          )}
        </div>
      </div>

      {mode !== "claude" && (
        <div className="chat-mode">
          {mode === "keyword"
            ? "Claude is off, so replies show the closest contract wording from a free keyword scan."
            : "Claude needs setup, so messages will fail until the status above says Claude is on."}
        </div>
      )}
      {error && <div className="error-banner">{error}</div>}

      <div className="chat-log" aria-live="polite">
        {count === 0 && (
          <div className="empty">Ask what the contract says. Answers quote the wording they rely on.</div>
        )}
        {chat?.messages.map((m) => (
          <div key={m.id} className={`chat-message chat-${m.role}`}>
            <div className="chat-bubble">
              {m.not_stated && <div className="faint" style={{ fontSize: 11 }}>Not stated in the documents</div>}
              {m.text}
              {m.citations.map((c, i) => (
                <div className="chat-citation" key={i}>
                  <blockquote>{c.quote}</blockquote>
                  <Link className="link" href={citationHref(c)}>{c.filename}, page {c.page}</Link>
                </div>
              ))}
            </div>
          </div>
        ))}
        {thinking && <div className="chat-message chat-assistant"><div className="chat-bubble muted">Reading the contract…</div></div>}
        {chat?.status === "failed" && <div className="error-banner">That answer did not finish: {chat.error}</div>}
        <div ref={end} />
      </div>

      <form className="chat-form" onSubmit={send}>
        <textarea className="input" rows={2} value={message} maxLength={2000} aria-label="Message to Claude"
          placeholder="Can the lender cancel on a change of control?" onChange={(e) => setMessage(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              e.currentTarget.form?.requestSubmit();
            }
          }} />
        <button className="btn btn-primary" disabled={busy || thinking || message.trim().length < 2}>Send</button>
      </form>
      <div className="faint chat-note">Check answers against the contract. This is not legal advice.</div>
    </section>
  );
}
