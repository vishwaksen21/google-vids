
// The operator's name, sent as X-Actor on every change so the audit trail names a person.
// Stored in localStorage (versioned key); this attributes actions, it does not
// authenticate anyone (the backend has no identity provider yet).

import { useSyncExternalStore } from "react";

const KEY = "vidsprod.operator.v1";
const EVENT = "vidsprod:operator";
const PATTERN = /^[\p{L}\p{N}_ .@'-]{1,80}$/u;

export function isValidOperator(name: string | null | undefined): name is string {
  if (!name) return false;
  const trimmed = name.trim();
  return trimmed.length >= 1 && PATTERN.test(trimmed);
}

export function getOperator(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function setOperator(name: string): void {
  try {
    window.localStorage.setItem(KEY, name.trim());
  } catch {
    // Storage blocked: the name lasts only for this page view.
  }
  window.dispatchEvent(new Event(EVENT));
}

export function clearOperator(): void {
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
  window.dispatchEvent(new Event(EVENT));
}

function subscribe(cb: () => void): () => void {
  window.addEventListener(EVENT, cb);
  window.addEventListener("storage", cb);
  return () => {
    window.removeEventListener(EVENT, cb);
    window.removeEventListener("storage", cb);
  };
}

/** Returns the operator name; `undefined` during server render (not yet known). */
export function useOperator(): string | null | undefined {
  return useSyncExternalStore(subscribe, getOperator, () => undefined);
}

export const OPERATOR_PROMPT_EVENT = "vidsprod:operator-prompt";

export function requestOperatorPrompt(): void {
  window.dispatchEvent(new Event(OPERATOR_PROMPT_EVENT));
}
