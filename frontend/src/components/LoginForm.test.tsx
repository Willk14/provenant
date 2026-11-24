import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LoginForm, safeNext } from "@/components/LoginForm";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams("next=/agents"),
}));
vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> }));

describe("LoginForm", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("only returns people to a page of this app", () => {
    expect(safeNext("/clients/abc")).toBe("/clients/abc");
    expect(safeNext("https://evil.test")).toBe("/documents");
    expect(safeNext("//evil.test/path")).toBe("/documents");
    expect(safeNext("/login")).toBe("/documents");
    expect(safeNext(null)).toBe("/documents");
  });

  it("offers the demo people when sign-in is not configured", () => {
    render(<LoginForm />);
    expect(screen.getByText(/Development mode/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Continue as Rae Otieno/ }));
    expect(push).toHaveBeenCalledWith("/agents");
  });
});
