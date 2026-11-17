"use client";

import { useEffect, useState } from "react";

import { currentPersona, onPersonaChange, type Persona } from "./identity";

/** null until mounted: the persona lives in localStorage, which the server cannot see. */
export function usePersona(): Persona | null {
  const [persona, setPersona] = useState<Persona | null>(null);
  useEffect(() => {
    setPersona(currentPersona());
    return onPersonaChange(() => setPersona(currentPersona()));
  }, []);
  return persona;
}
