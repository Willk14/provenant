"use client";

import { useEffect, useState } from "react";

import { ContractChat } from "@/components/ContractChat";
import { api } from "@/lib/api";
import type { AiAgents } from "@/lib/types";

/** The contract chat, inside the contract it is about. */
export function AskTab({ contractId, filename }: { contractId: string; filename: string }) {
  const [mode, setMode] = useState<AiAgents["engine"]["mode"] | null>(null);
  useEffect(() => {
    api.aiAgents().then((a) => setMode(a.engine.mode)).catch(() => setMode("needs_setup"));
  }, []);
  if (mode === null) return <div className="empty">Loading…</div>;
  return <ContractChat contractId={contractId} filename={filename} mode={mode} />;
}
