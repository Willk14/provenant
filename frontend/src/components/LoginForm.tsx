"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { AUTH_MODE, callbackUrl, supabase } from "@/lib/auth";
import { PERSONAS, setPersona } from "@/lib/identity";

/** Only send people back to a page of this app, never to another site. */
export function safeNext(next: string | null): string {
  return next && next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/login") ? next : "/home";
}

export function LoginForm() {
  const router = useRouter();
  const next = safeNext(useSearchParams().get("next"));
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    void supabase()?.auth.getSession().then(({ data }) => data.session && router.replace(next));
  }, [next, router]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const client = supabase();
  return (
    <div className="auth-page">
      <Link href="/" className="brand auth-brand">
        <span className="brand-mark" aria-hidden>P</span>
        Provenant
      </Link>
      <div className="card auth-card">
        <h1>Sign in</h1>
        {AUTH_MODE === "dev" || !client ? (
          <>
            <p className="muted">
              Development mode: choose a demo person. Real sign-in turns on when the Supabase settings are added.
            </p>
            <div className="auth-personas">
              {PERSONAS.map((p) => (
                <button key={p.key} type="button" className="btn auth-persona" onClick={() => {
                  setPersona(p.key);
                  router.push(next);
                }}>
                  <b>Continue as {p.name}</b>
                  <span className="faint">{p.blurb}</span>
                </button>
              ))}
            </div>
          </>
        ) : (
          <form onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              const { error: failed } = await client.auth.signInWithPassword({ email, password });
              if (failed) throw failed;
              router.replace(next);
            });
          }}>
            <p className="muted">Use the email your firm invited.</p>
            <label className="field">
              Email
              <input className="input" type="email" autoComplete="email" required value={email}
                onChange={(e) => setEmail(e.target.value)} />
            </label>
            <label className="field">
              Password
              <input className="input" type="password" autoComplete="current-password" value={password}
                onChange={(e) => setPassword(e.target.value)} />
            </label>
            {error && <div className="error-banner">{error}</div>}
            {notice && <div className="callout callout-ok">{notice}</div>}
            <button className="btn btn-primary auth-submit" disabled={busy || !email || !password}>Sign in</button>
            <div className="auth-alt">
              <button type="button" className="btn" disabled={busy || !email} onClick={() => void run(async () => {
                const { error: failed } = await client.auth.signInWithOtp({
                  email, options: { shouldCreateUser: false, emailRedirectTo: callbackUrl() },
                });
                if (failed) throw failed;
                setNotice(`If ${email} has an account, a sign-in link is on its way.`);
              })}>
                Email me a sign-in link
              </button>
              <button type="button" className="btn btn-ghost" disabled={busy || !email} onClick={() => void run(async () => {
                const { error: failed } = await client.auth.resetPasswordForEmail(email, { redirectTo: callbackUrl() });
                if (failed) throw failed;
                setNotice(`If ${email} has an account, a password reset link is on its way.`);
              })}>
                Forgot password?
              </button>
            </div>
            <p className="faint auth-note">New to your firm&apos;s workspace? Ask a partner to invite you.</p>
          </form>
        )}
      </div>
    </div>
  );
}
