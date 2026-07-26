/**
 * Background: AsterMem UI supports ten languages (English as the source language),
 * and the project convention forbids third-party i18n libraries like i18next.
 * Design intent: Uses a minimal dictionary pattern where the English text is the key,
 * dictionaries are split by language under src/locales/; when t() can't find an entry
 * it returns the English text directly, so missing translations degrade gracefully to English.
 * Key constraint: New text must be added to all languages (validated by scripts/check-i18n.mjs);
 * dictionaries must not contain emoji.
 */
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { IconChevronDown } from "@tabler/icons-react";
import { DICTS } from "./locales";

// Language list ordered alphabetically by locale code; switch menu shows this order
export const SUPPORTED_LOCALES = [
  "de", "en", "es", "fr", "ja", "ko", "pt", "ru", "zh-CN", "zh-TW",
] as const;
export type Locale = (typeof SUPPORTED_LOCALES)[number];

const LABELS: Record<Locale, string> = {
  en: "English",
  "zh-CN": "简体中文",
  "zh-TW": "繁體中文",
  ja: "日本語",
  ko: "한국어",
  fr: "Français",
  de: "Deutsch",
  es: "Español",
  pt: "Português",
  ru: "Русский",
};

/** Short labels displayed on the top-right switch button */
const SHORT_LABELS: Record<Locale, string> = {
  en: "EN",
  "zh-CN": "简中",
  "zh-TW": "繁中",
  ja: "JA",
  ko: "KO",
  fr: "FR",
  de: "DE",
  es: "ES",
  pt: "PT",
  ru: "RU",
};

const STORAGE_KEY = "astermem_locale";

/**
 * Background: localStorage and browser language may provide various locale code formats.
 * Design intent: Normalize common locale codes to supported languages, defaulting to English,
 * ensuring any dirty input never puts the UI into an unknown language state.
 * Key constraint: Return value must be a member of SUPPORTED_LOCALES.
 */
function normalizeLocale(value: string | null | undefined): Locale {
  if (SUPPORTED_LOCALES.includes(value as Locale)) return value as Locale;
  const lower = String(value || "").toLowerCase();
  if (lower === "zh-tw" || lower === "zh-hk" || lower === "zh-mo" || lower === "zh-hant") return "zh-TW";
  if (lower.startsWith("zh")) return "zh-CN";
  for (const code of ["ja", "ko", "fr", "de", "es", "pt", "ru"] as const) {
    if (lower.startsWith(code)) return code;
  }
  return "en";
}

export type TFunc = (key: string, vars?: Record<string, string | number>) => string;

interface I18nValue {
  locale: Locale;
  setLocale: (next: string) => void;
  t: TFunc;
}

const I18nContext = createContext<I18nValue | null>(null);

/**
 * Background: Language state needs to be shared across pages and persist after refresh.
 * Design intent: Context holds locale and t(); localStorage only stores the language code;
 * syncs document.lang for font stacks and accessibility tools.
 * Key constraint: t()'s {var} interpolation is simple string replacement only, no pluralization logic.
 */
export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(() =>
    normalizeLocale(window.localStorage.getItem(STORAGE_KEY) || navigator.language),
  );

  const setLocale = (next: string) => {
    const normalized = normalizeLocale(next);
    window.localStorage.setItem(STORAGE_KEY, normalized);
    setLocaleState(normalized);
  };

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const value = useMemo<I18nValue>(() => {
    // English also has a small patch dictionary: backend profile field labels/hints
    // are sent in Chinese, so the English UI also needs translations for those Chinese keys.
    const dict = DICTS[locale];
    const t: TFunc = (key, vars) => {
      let text = dict?.[key] || key;
      if (vars) {
        for (const [k, v] of Object.entries(vars)) {
          text = text.split(`{${k}}`).join(String(v));
        }
      }
      return text;
    };
    return { locale, setLocale, t };
  }, [locale]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

/**
 * Background: All user-visible text must go through the current language Context.
 * Design intent: Throws immediately when Provider is missing, exposing integration issues during development.
 * Key constraint: Can only be called within the I18nProvider subtree.
 */
export function useI18n(): I18nValue {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useI18n must be used within I18nProvider");
  return value;
}

/**
 * Background: The top-right corner needs a mono small-text dropdown for switching languages.
 * Design intent: Controlled popover + click-outside-to-close, styling follows Asterove's locale-switcher.
 * Key constraint: Icons use Tabler locally-bundled SVGs, not emoji; menu closes immediately on selection.
 */
export function LocaleSwitcher() {
  const { locale, setLocale } = useI18n();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [open]);

  return (
    <div className="locale-switcher" onClick={(e) => e.stopPropagation()}>
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span>{SHORT_LABELS[locale]}</span>
        <IconChevronDown aria-hidden="true" />
      </button>
      {open && (
        <div>
          {SUPPORTED_LOCALES.map((item) => (
            <button
              type="button"
              key={item}
              className={locale === item ? "active" : ""}
              onClick={() => {
                setLocale(item);
                setOpen(false);
              }}
            >
              {LABELS[item]}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
