import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AgentsView, formatUsd } from "@/components/AgentsView";
import { api, ApiError } from "@/lib/api";
import type { AiAgents } from "@/lib/types";

const push = vi.fn();
const persona = { current: { key: "pam", name: "Pam", user: "pam@demo-firm", roles: ["partner"], blurb: "" } };
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> }));
vi.mock("@/lib/usePersona", () => ({ usePersona: () => persona.current }));
vi.mock("@/lib/api", () => {
  class MockApiError extends Error {
    constructor(readonly status: number, message: string) {
      super(message);
    }
  }
  return { ApiError: MockApiError, api: { aiAgents: vi.fn(), aiUsage: vi.fn() } };
});

const data: AiAgents = {
  engine: { mode: "keyword", label: "Free keyword reader", model: null, detail: "Claude is not on." },
  agents: [
    { key: "reviewer", name: "Contract reviewer", description: "Reviews.", tab: "review", available: true, note: null, runs: 2, waiting: 3 },
    { key: "drafter", name: "Paralegal drafter", description: "Drafts.", tab: "drafts", available: true, note: null, runs: 1, waiting: 1 },
  ],
  contracts: [{ id: "c1", filename: "facility.pdf", kind: "credit_agreement" }],
  activity: [{ kind: "draft", id: "d1", document_id: "c1", filename: "facility.pdf", title: "Notice (skeleton)", status: "ready", agent: "baseline", by: "rae", at: "2026-11-23T21:46:00Z" }],
};

describe("AgentsView", () => {
  beforeEach(() => {
    persona.current = { key: "pam", name: "Pam", user: "pam@demo-firm", roles: ["partner"], blurb: "" };
    vi.mocked(api.aiAgents).mockResolvedValue(data);
    vi.mocked(api.aiUsage).mockResolvedValue({ total_runs: 2, failed_runs: 0, unpriced_runs: 0, estimated_cost_microusd: 32_800 });
  });
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("says plainly that Claude is off and shows the agents and their work", async () => {
    render(<AgentsView />);
    expect(await screen.findByText("Free keyword reader")).toBeTruthy();
    expect(screen.getByText("Free mode")).toBeTruthy();
    expect(screen.getByText("Contract reviewer")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Notice (skeleton)" }).getAttribute("href")).toBe("/documents/c1?tab=drafts");
  });

  it("opens the chosen contract on the agent's tab", async () => {
    render(<AgentsView />);
    fireEvent.click(await screen.findByRole("button", { name: "Prepare a draft" }));
    expect(push).toHaveBeenCalledWith("/documents/c1?tab=drafts");
  });

  it("shows estimated spend to partners only", async () => {
    render(<AgentsView />);
    expect(await screen.findByText("USD 0.0328")).toBeTruthy(); // 32,800 micro-dollars
    cleanup();
    persona.current = { ...persona.current, key: "pat", roles: ["preparer"] };
    vi.mocked(api.aiUsage).mockClear();
    render(<AgentsView />);
    await screen.findByText("Free keyword reader");
    expect(api.aiUsage).not.toHaveBeenCalled();
  });

  it("asks for a restart when the running API predates this page", async () => {
    vi.mocked(api.aiAgents).mockRejectedValue(new ApiError(404, "Not Found"));
    render(<AgentsView />);
    await waitFor(() => expect(screen.getByText("Restart the API to turn this page on")).toBeTruthy());
  });

  it("formats micro-dollars without a float", () => {
    expect(formatUsd(5_000_000)).toBe("USD 5.0000");
    expect(formatUsd(16_400)).toBe("USD 0.0164");
  });
});
