/** To review: everything in the firm waiting for a person (ADR 0021). */
import { api } from "./api";

export interface TodoItem {
  kind: "deadline" | "mapping" | "figures" | "findings" | "draft" | "conflict" | "notice" | "precedent" | "trust" | "kyc";
  title: string;
  detail: string;
  href: string;
  prepared_by: string;
  by_claude: boolean;
  yours: boolean;
  why_not: string;
  due: string | null;
  urgent: boolean;
}

export interface Todo {
  items: TodoItem[];
  yours: number;
  waiting_on_others: number;
}

export const KIND_LABEL: Record<TodoItem["kind"], string> = {
  deadline: "Deadline",
  mapping: "Tax mapping",
  figures: "Figures",
  findings: "Contract review",
  draft: "Draft",
  conflict: "Conflict check",
  notice: "Court notice",
  precedent: "Precedent draft",
  trust: "Trust account",
  kyc: "KYC",
};

export const todoApi = {
  todo: () => api.raw<Todo>("/todo"),
};
