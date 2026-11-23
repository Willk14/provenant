import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ContractChat } from "@/components/ContractChat";
import { api } from "@/lib/api";
import type { ContractChat as Chat } from "@/lib/types";

vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> }));
vi.mock("@/lib/api", () => ({ api: { contractChats: vi.fn(), startContractChat: vi.fn(), sendChatMessage: vi.fn() } }));

const citation = { document_id: "c1", filename: "facility.pdf", quote: "the Lender may cancel", page: 2, bbox: [1, 2, 3, 4] as [number, number, number, number] };
const chat: Chat = {
  id: "t1", document_id: "c1", title: "Can they cancel?", status: "idle", error: null, created_at: "2026-11-23T10:00:00Z",
  messages: [
    { id: "m1", role: "user", text: "Can they cancel?", citations: [], not_stated: false, author: "pat", at: "2026-11-23T10:00:00Z" },
    { id: "m2", role: "assistant", text: "Yes, the Lender may.", citations: [citation], not_stated: false, author: "claude", at: "2026-11-23T10:00:05Z" },
  ],
};

describe("ContractChat", () => {
  beforeEach(() => {
    vi.mocked(api.contractChats).mockResolvedValue([chat]);
    vi.mocked(api.startContractChat).mockResolvedValue({ ...chat, id: "t2", status: "queued" });
    vi.mocked(api.sendChatMessage).mockResolvedValue({ ...chat, status: "queued" });
  });
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows the quote and links it to its place on the PDF", async () => {
    render(<ContractChat contractId="c1" filename="facility.pdf" mode="claude" />);
    expect(await screen.findByText("the Lender may cancel")).toBeTruthy();
    expect(screen.getByRole("link", { name: "facility.pdf, page 2" }).getAttribute("href"))
      .toBe("/documents/c1?tab=review&page=2&bbox=1,2,3,4");
  });

  it("says when Claude is off instead of pretending to be it", async () => {
    render(<ContractChat contractId="c1" filename="facility.pdf" mode="keyword" />);
    expect(await screen.findByText(/Claude is off/)).toBeTruthy();
  });

  it("continues the open chat and can start a new one", async () => {
    render(<ContractChat contractId="c1" filename="facility.pdf" mode="claude" />);
    await screen.findByText("Yes, the Lender may.");
    fireEvent.change(screen.getByLabelText("Message to Claude"), { target: { value: "On what event?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(api.sendChatMessage).toHaveBeenCalledWith("c1", "t1", "On what event?"));

    vi.mocked(api.contractChats).mockResolvedValue([chat]);
    fireEvent.click(await screen.findByRole("button", { name: "New chat" }));
    fireEvent.change(screen.getByLabelText("Message to Claude"), { target: { value: "A new question" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(api.startContractChat).toHaveBeenCalledWith("c1", "A new question"));
  });
});
