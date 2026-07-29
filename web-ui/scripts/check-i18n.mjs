// i18n key integrity check. Keys are the English source strings, so no translated dictionary is
// authoritative: the reference key set is the union of every locale, and a key missing from one file
// is reported against it. That also means a typo'd key surfaces as "missing in the other nine"
// rather than silently becoming the baseline, which is what happened while one language held that
// role. en.ts is excluded because English needs no entries — t() falls back to the key.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const LANGS = ["zh-CN", "zh-TW", "ko", "ja", "fr", "de", "es", "pt", "ru"];

function loadDict(lang) {
  const source = readFileSync(join(root, `src/locales/${lang}.ts`), "utf-8");
  const start = source.indexOf("{", source.indexOf("dict"));
  const end = source.lastIndexOf("}");
  return new Function(`return ${source.slice(start, end + 1)}`)();
}

const placeholders = (text) =>
  [...String(text).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(",");

const dicts = new Map(LANGS.map((lang) => [lang, loadDict(lang)]));
const reference = new Set(LANGS.flatMap((lang) => Object.keys(dicts.get(lang))));
let failed = false;

for (const lang of LANGS) {
  const dict = dicts.get(lang);
  const keys = new Set(Object.keys(dict));
  const missing = [...reference].filter((k) => !keys.has(k));
  const badVars = [...keys].filter(
    (k) => placeholders(k) !== placeholders(dict[k]) && placeholders(k) !== "",
  );
  const emoji = [...keys].filter((k) => /\p{Extended_Pictographic}/u.test(dict[k]));
  if (missing.length || badVars.length || emoji.length) {
    failed = true;
    console.log(`\n=== ${lang} ===`);
    if (missing.length) console.log(`  ${missing.length} missing keys:`, missing.slice(0, 10));
    if (badVars.length) console.log(`  ${badVars.length} keys with mismatched placeholders:`, badVars.slice(0, 10));
    if (emoji.length) console.log(`  ${emoji.length} keys containing emoji:`, emoji.slice(0, 10));
  } else {
    console.log(`${lang}: OK (${keys.size} keys)`);
  }
}

console.log(`\nreference key set: ${reference.size} keys (union of ${LANGS.length} locales)`);
process.exit(failed ? 1 : 0);
