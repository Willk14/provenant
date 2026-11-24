"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { supabase } from "@/lib/auth";

/** Where emailed links land: sign-in links sign in, while invitations and resets ask for a new password first. */
export default function AuthCallback() {
  const router = useRouter();
  const [state, setState] = useState<"checking" | "password" | "failed">("checking");
  const [message, setMessage] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const hash = new URLSearchParams(window.location.hash.slice(1));
    const query = new URLSearchParams(window.location.search);
    const failure = hash.get("error_description") ?? query.get("error_description");
    if (failure) {
      setMessage(failure);
      setState("failed");
      return;
    }
    const needsPassword = ["invite", "recovery"].includes(hash.get("type") ?? "");
    const client = supabase();
    if (!client) {
      router.replace("/login");
      return;
    }
    const settle = (signedIn: boolean) => {
      if (!signedIn) return;
      if (needsPassword) setState("password");
      else router.replace("/home");
    };
    void client.auth.getSession().then(({ data }) => settle(!!data.session));
    const { data } = client.auth.onAuthStateChange((event, session) => {
      if (event === "PASSWORD_RECOVERY") setState("password");
      else settle(!!session);
    });
    const timer = setTimeout(() => setState((s) => (s === "checking" ? "failed" : s)), 8000);
    return () => {
      clearTimeout(timer);
      data.subscription.unsubscribe();
    };
  }, [router]);

  return (
    <div className="auth-page">
      <Link href="/" className="brand auth-brand">
        <span className="brand-mark" aria-hidden>P</span>
        Provenant
      </Link>
      <div className="card auth-card">
        {state === "checking" && <><h1>Signing you in…</h1><p className="muted">This takes a moment.</p></>}
        {state === "failed" && (
          <>
            <h1>That link did not work</h1>
            <p className="muted">{message ?? "It may have expired or already been used."}</p>
            <Link className="btn btn-primary" href="/login">Back to sign in</Link>
          </>
        )}
        {state === "password" && (
          <form onSubmit={(e) => {
            e.preventDefault();
            setBusy(true);
            void supabase()?.auth.updateUser({ password }).then(({ error }) => {
              setBusy(false);
              if (error) setMessage(error.message);
              else router.replace("/home");
            });
          }}>
            <h1>Choose a password</h1>
            <p className="muted">You will use it with your email to sign in.</p>
            <label className="field">
              New password
              <input className="input" type="password" autoComplete="new-password" minLength={10} required
                value={password} onChange={(e) => setPassword(e.target.value)} />
            </label>
            {message && <div className="error-banner">{message}</div>}
            <button className="btn btn-primary auth-submit" disabled={busy || password.length < 10}>Save and continue</button>
          </form>
        )}
      </div>
    </div>
  );
}
