import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DraftingPanel } from "@/components/DraftingPanel";
import { api } from "@/lib/api";
import type { ContractDraft, DocumentSummary } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  api: {
    contractDrafts: vi.fn(),
    startContractDraft: vi.fn(),
    editContractDraft: vi.fn(),
    decideContractDraft: vi.fn(),
  },
}));

const doc = { id: "doc-1", kind: "credit_agreement", filename: "facility.pdf" } as DocumentSummary;
const citation = { document_id: "doc-1", filename: "facility.pdf", quote: "the Lender may cancel", page: 2, bbox: [1, 2, 3, 4] as [number, number, number, number] };
const draft: ContractDraft = {
  id: "d1", document_id: "doc-1", kind: "notice", instructions: "Cancel the facility.", status: "ready",
  agent: "claude-api:claude-opus-5-5", title: "Notice of cancellation",
  paragraphs: [{ text: "The Lender may cancel the facility.", citations: [citation], edited: false },
               { text: "Signed: [[signatory]]", citations: [], edited: false }],
  open_points: ["Check service of notice."], version: 1, content_sha256: "a", error: null, requested_by: "rae",
  created_at: "2026-11-23T10:00:00Z",
  history: [{ action: "generated", version: 1, actor: "claude-api", note: null, content_sha256: "a", at: "2026-11-23T10:00:00Z" }],
};

describe("DraftingPanel", () => {
  beforeEach(() => {
    vi.mocked(api.contractDrafts).mockResolvedValue([draft]);
    vi.mocked(api.startContractDraft).mockResolvedValue({ ...draft, id: "d2", status: "queued" });
    vi.mocked(api.editContractDraft).mockResolvedValue(draft);
    vi.mocked(api.decideContractDraft).mockResolvedValue(draft);
  });
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("marks what is missing, links citations and blocks approval until it is filled in", async () => {
    const onCite = vi.fn();
    render(<DraftingPanel doc={doc} onCite={onCite} />);
    expect((await screen.findByText("[[signatory]]")).tagName).toBe("MARK");
    expect(screen.getByText("Check service of notice.")).toBeTruthy();
    expect(screen.getByText(/1 part still to fill in/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Approve" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByTitle("Show on the PDF"));
    expect(onCite).toHaveBeenCalledWith("d1-0-0", citation);
  });

  it("starts a draft only when a person asks", async () => {
    render(<DraftingPanel doc={doc} onCite={vi.fn()} />);
    await screen.findByText("Notice of cancellation");
    expect(api.startContractDraft).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("What must it do?"), { target: { value: "Ask for the overdue certificate." } });
    fireEvent.click(screen.getByRole("button", { name: "Prepare draft" }));
    await waitFor(() => expect(api.startContractDraft).toHaveBeenCalledWith("doc-1", "notice", "Ask for the overdue certificate."));
  });

  it("saves edited wording and needs a reason to return a draft", async () => {
    render(<DraftingPanel doc={doc} onCite={vi.fn()} />);
    const back = (await screen.findByRole("button", { name: "Return" })) as HTMLButtonElement;
    expect(back.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Note on this draft"), { target: { value: "Name the signatory" } });
    fireEvent.click(back);
    await waitFor(() => expect(api.decideContractDraft).toHaveBeenCalledWith("doc-1", "d1", "returned", "Name the signatory"));
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Paragraph 2"), { target: { value: "Signed: A. Director" } });
    fireEvent.click(screen.getByRole("button", { name: "Save edits" }));
    await waitFor(() => expect(api.editContractDraft).toHaveBeenCalledWith("doc-1", "d1", "Notice of cancellation", [
      "The Lender may cancel the facility.", "Signed: A. Director",
    ]));
  });
});
