/**
 * Background: AsterMem UI supports ten languages, with dictionaries split into separate files per language
 * for per-language maintenance and parallel translation by multiple people (or agents).
 * Design intent: English is the source language and doesn't need a dictionary; when t() can't find an entry
 * it falls back to the English original, so a missing translation only locally degrades to English, never crashes.
 * Key constraint: All language files' keys must match zh-CN.ts (validated by scripts/check-i18n.mjs); no emoji.
 */
import en from "./en";
import ko from "./ko";
import zhTW from "./zh-TW";
import zhCN from "./zh-CN";
import ja from "./ja";
import fr from "./fr";
import ru from "./ru";
import de from "./de";
import es from "./es";
import pt from "./pt";

export const DICTS: Record<string, Record<string, string>> = {
  en,
  ko,
  "zh-TW": zhTW,
  "zh-CN": zhCN,
  ja,
  fr,
  ru,
  de,
  es,
  pt,
};
