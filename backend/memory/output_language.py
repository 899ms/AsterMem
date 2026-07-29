"""
Output language for model-generated text

Background: the UI ships in ten languages, but the chosen locale lived only in the browser's
localStorage and never reached the backend. Every prompt in the project is written in English and
none of them said which language to answer in, so models picked one from the input text: a user
reading a Chinese UI got English chunk summaries, English semantic tags and English follow-up
suggestions, sometimes mixed with Chinese ones inside a single batch.
Design intent: config.yaml carries one output_language field that every prompt site reads, and
this module turns it into a sentence appended to the prompt. The instruction itself stays in
English because models follow English instructions most reliably; only the generated content
follows the user.
Key constraints:
  - "auto" reproduces the historical behaviour (no instruction at all), for users who want each
    memory summarized in whatever language that memory is written in
  - Structured call sites parse the reply with code, so their instruction has to also state that
    JSON field names stay English — a translated key silently breaks extraction. Those sites pass
    json_mode=True
  - Only prompts whose output reaches the user get an instruction. Internal prompts that return
    numbers or search keywords are deliberately left alone, since asking for another language
    there breaks parsing rather than helping anyone
  - The live config dict is bound once at startup rather than passed down through call sites.
    Chunkers, meta extractors and the explorer are process-lifetime singletons holding their own
    model handles, so a value copied into them at construction would go stale the moment the user
    switches language; reading the bound dict means a switch takes effect on the next prompt
    without anyone having to remember to refresh those instances

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

from typing import Optional

AUTO = "auto"

# Codes mirror SUPPORTED_LOCALES in web-ui/src/i18n.tsx. Names are the English ones on purpose:
# the whole prompt is English, and a script sample would be the only non-ASCII text in the file
# for no gain, since every model resolves these labels unambiguously.
LANGUAGE_NAMES = {
    "en": "English",
    "zh-CN": "Simplified Chinese",
    "zh-TW": "Traditional Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "pt": "Portuguese",
    "ru": "Russian",
}


def normalize(value) -> str:
    """
    Map any locale-shaped input onto a supported code, falling back to AUTO.

    The frontend sends its own locale verbatim and config.yaml is hand-editable, so region
    variants (zh_TW, en-GB) and junk both have to land somewhere safe rather than reaching a
    prompt as an unknown language name.
    """
    if not isinstance(value, str):
        return AUTO
    candidate = value.strip()
    if not candidate or candidate.lower() == AUTO:
        return AUTO
    if candidate in LANGUAGE_NAMES:
        return candidate
    lowered = candidate.lower().replace("_", "-")
    if lowered in {"zh-tw", "zh-hk", "zh-mo", "zh-hant"}:
        return "zh-TW"
    if lowered.startswith("zh"):
        return "zh-CN"
    base = lowered.split("-")[0]
    return base if base in LANGUAGE_NAMES else AUTO


def resolve(config: Optional[dict]) -> str:
    """Read the configured output language, normalized."""
    return normalize((config or {}).get("output_language"))


def directive(language: Optional[str], json_mode: bool = False) -> str:
    """
    The sentence to append to a prompt so its output lands in the user's language.

    Returns an empty string for AUTO and for unsupported input, which keeps every call site a
    plain concatenation with no branching.
    """
    code = normalize(language)
    if code == AUTO:
        return ""
    name = LANGUAGE_NAMES[code]
    if json_mode:
        return (
            f"\n\n[Output Language]\nWrite every human-readable value in {name}, whatever the "
            f"language of the source text. Keep the JSON field names exactly as written above, "
            f"in English."
        )
    return (
        f"\n\n[Output Language]\nWrite your entire response in {name}, whatever the language of "
        f"the source text."
    )


_active_config: Optional[dict] = None


def bind(config: Optional[dict]) -> None:
    """
    Register the live config dict as the source of truth for every prompt site.

    Called once during startup with the same dict the API mutates in place, so a language change
    saved from the settings page is visible here without any further plumbing. Passing None
    unbinds, which is what tests use to get back to AUTO.
    """
    global _active_config
    _active_config = config


def current() -> str:
    """The configured output language right now."""
    return resolve(_active_config)


def current_directive(json_mode: bool = False) -> str:
    """The directive to append to a prompt, for the language configured right now."""
    return directive(current(), json_mode=json_mode)
