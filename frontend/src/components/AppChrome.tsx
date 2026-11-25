"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Sidebar } from "@/components/Sidebar";
import { api } from "@/lib/api";
import { AUTH_MODE, PUBLIC_PATHS, supabase } from "@/lib/auth";

/** The app's frame: public pages stand alone; app pages get the sidebar and, with Supabase, need a signed-in user. */
export function AppChrome({ children }: { children: React.ReactNode }) {
  const path = usePathname() ?? "/";
  const router = useRouter();
  const isPublic = PUBLIC_PATHS.includes(path);
  const [allowed, setAllowed] = useState(AUTH_MODE === "dev");

  useEffect(() => {
    const client = supabase();
    if (isPublic || !client) {
      setAllowed(true);
      return;
    }
    const toLogin = () => router.replace(`/login?next=${encodeURIComponent(path)}`);
    void client.auth.getSession().then(({ data }) => (data.session ? setAllowed(true) : toLogin()));
    const { data } = client.auth.onAuthStateChange((event, session) => {
      if (!session && event === "SIGNED_OUT") toLogin();
    });
    return () => data.subscription.unsubscribe();
  }, [isPublic, path, router]);

  // Record the person in the firm's directory once they are in, so notifications and the inbox know them.
  useEffect(() => {
    if (allowed && !isPublic) void api.me().catch(() => undefined);
  }, [allowed, isPublic]);

  if (isPublic) return <>{children}</>;
  return (
    <>
      <div className="app-shell">
        <Sidebar />
        <div className="app-main">{allowed ? children : <div className="empty">Checking your sign-in…</div>}</div>
      </div>
    </>
  );
}
