/**
 * Development identities. The API (with PROVENANT_DEV_AUTH=1) trusts these headers.
 * shortcut: replace with an OIDC sign-in that stores a bearer token before any shared deployment.
 */
export interface Persona {
  key: string;
  name: string;
  user: string;
  roles: string[];
  blurb: string;
}

export const TENANT = "demo-firm";

export const PERSONAS: Persona[] = [
  { key: "pat", name: "Pat Mwangi", user: "pat@demo-firm", roles: ["preparer"], blurb: "Preparer: uploads and enters" },
  { key: "rae", name: "Rae Otieno", user: "rae@demo-firm", roles: ["reviewer"], blurb: "Reviewer: checks extracted figures" },
  { key: "pam", name: "Pam Kariuki", user: "pam@demo-firm", roles: ["partner"], blurb: "Partner: signs off judgement calls" },
];

const STORAGE_KEY = "provenant.persona";
const listeners = new Set<() => void>();

export function currentPersona(): Persona {
  let key: string | null = null;
  try {
    key = window.localStorage.getItem(STORAGE_KEY);
  } catch {
    // storage blocked: fall back to the default persona
  }
  return PERSONAS.find((p) => p.key === key) ?? PERSONAS[0];
}

export function setPersona(key: string): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, key);
  } catch {
    // ignore: the choice just won't persist
  }
  listeners.forEach((fn) => fn());
}

export function onPersonaChange(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function identityHeaders(p: Persona = currentPersona()): Record<string, string> {
  return { "X-Tenant-Id": TENANT, "X-User": p.user, "X-Roles": p.roles.join(",") };
}
