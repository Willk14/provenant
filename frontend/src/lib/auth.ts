/**
 * Sign-in. With NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY set, people sign in with Supabase
 * Auth and every API call carries their access token. Without them, the app runs on the DEV personas.
 * Only the publishable key belongs here: anything NEXT_PUBLIC_ is sent to the browser.
 */
import { createClient, type Session, type SupabaseClient } from "@supabase/supabase-js";

const URL = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
const KEY = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ?? "";

export const AUTH_MODE: "supabase" | "dev" = URL && KEY ? "supabase" : "dev";
export const PUBLIC_PATHS = ["/", "/login", "/auth/callback"];

let client: SupabaseClient | null = null;

export function supabase(): SupabaseClient | null {
  if (AUTH_MODE !== "supabase") return null;
  client ??= createClient(URL, KEY, {
    auth: { flowType: "implicit", persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
  });
  return client;
}

export async function currentSession(): Promise<Session | null> {
  const c = supabase();
  if (!c) return null;
  const { data } = await c.auth.getSession();
  return data.session;
}

export async function authHeaders(): Promise<Record<string, string> | null> {
  const session = await currentSession();
  return session ? { Authorization: `Bearer ${session.access_token}` } : null;
}

export function callbackUrl(): string {
  return `${window.location.origin}/auth/callback`;
}
