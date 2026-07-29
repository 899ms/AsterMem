// English is the source language: every i18n key in the project is already the English string, and
// t() falls back to the key when no entry exists, so this dictionary is intentionally empty. It is
// kept as a module so DICTS has an "en" member and locale switching needs no special case.
// Add an entry here only to override a source string for English readers without touching the key
// (which would require re-keying all ten locale files).
const dict: Record<string, string> = {};

export default dict;
