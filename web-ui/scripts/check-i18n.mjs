// i18n key integrity check: uses zh-CN.ts as the baseline, compares key sets across the other
// locale files, and reports missing/extra keys plus entries with mismatched {var} placeholders.
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

const base = loadDict("zh-CN");
const baseKeys = new Set(Object.keys(base));
let failed = false;

for (const lang of LANGS.slice(1)) {
  const dict = loadDict(lang);
  const keys = new Set(Object.keys(dict));
  const missing = [...baseKeys].filter((k) => !keys.has(k));
  const extra = [...keys].filter((k) => !baseKeys.has(k));
  const badVars = [...keys].filter(
    (k) => baseKeys.has(k) && placeholders(k) !== placeholders(dict[k]) && placeholders(k) !== "",
  );
  const emoji = [...keys].filter((k) => /\p{Extended_Pictographic}/u.test(dict[k]));
  if (missing.length || extra.length || badVars.length || emoji.length) {
    failed = true;
    console.log(`\n=== ${lang} ===`);
    if (missing.length) console.log(`  ${missing.length} missing keys:`, missing.slice(0, 10));
    if (extra.length) console.log(`  ${extra.length} extra keys:`, extra.slice(0, 10));
    if (badVars.length) console.log(`  ${badVars.length} keys with mismatched placeholders:`, badVars.slice(0, 10));
    if (emoji.length) console.log(`  ${emoji.length} keys containing emoji:`, emoji.slice(0, 10));
  } else {
    console.log(`${lang}: OK (${keys.size} keys)`);
  }
}

process.exit(failed ? 1 : 0);
