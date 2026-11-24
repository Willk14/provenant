"use client";

import { useEffect, useState } from "react";

import { AUTH_MODE, supabase } from "./auth";
import { currentPersona, onPersonaChange, type Persona } from "./identity";

/** The person using the app: a signed-in Supabase user, or the chosen DEV persona. Null until known or signed out. */
export function usePersona(): Persona | null {
  const [persona, setPersona] = useState<Persona | null>(null);
  useEffect(() => {
    const client = supabase();
    if (AUTH_MODE !== "supabase" || !client) {
      setPersona(currentPersona());
      return onPersonaChange(() => setPersona(currentPersona()));
    }
    const fromSession = (session: Awaited<ReturnType<typeof client.auth.getSession>>["data"]["session"]) => {
      if (!session) return null;
      const roles = session.user.app_metadata?.roles;
      const email = session.user.email ?? "";
      return {
        key: session.user.id,
        name: email || "Signed in",
        user: session.user.id,
        roles: Array.isArray(roles) ? roles.filter((r): r is string => typeof r === "string") : [],
        blurb: email,
      };
    };
    void client.auth.getSession().then(({ data }) => setPersona(fromSession(data.session)));
    const { data } = client.auth.onAuthStateChange((_event, session) => setPersona(fromSession(session)));
    return () => data.subscription.unsubscribe();
  }, []);
  return persona;
}
