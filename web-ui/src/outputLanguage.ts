/**
 * Background: the language picker only wrote to localStorage, so the backend never learned which
 * language the reader had chosen. Every prompt in the project is written in English and none of
 * them named an output language, so chunk summaries, semantic tags and follow-up suggestions came
 * back in whatever language the model settled on — usually English, sometimes mixed.
 * Design intent: Mirror the UI locale into config.yaml, where every prompt site reads it. The sync
 * runs whenever the locale is known and a session exists, not only on switch: most readers never
 * touch the picker at all because their browser already reports their language, so a switch event
 * would never fire for them.
 * Key constraint: Failures stay silent. This is preference plumbing rather than a user action, and
 * a demo deployment rejects config writes outright — neither deserves an error toast.
 */
import { useEffect } from "react";
import { api } from "./api";

export function useOutputLanguageSync(locale: string, enabled: boolean): void {
  useEffect(() => {
    if (!enabled) return;
    api("PUT", "/api/config", { output_language: locale }, { skipAuthRedirect: true }).catch(
      (err) => console.debug("[AsterMem] output language sync skipped", err),
    );
  }, [locale, enabled]);
}
