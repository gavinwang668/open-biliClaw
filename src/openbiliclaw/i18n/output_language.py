"""Output-language policy for model-generated, user-visible text.

OpenBiliClaw's prompt-cache convention forbids putting per-call variables in
the system message. The active locale is per-call state, so it is expressed as
a compact directive appended to the *user* prompt — the system message stays
byte-identical across locales. That keeps provider prompt caching intact while
still telling the model which language the reader wants.

Only builders whose output a person reads (chat replies, recommendation copy,
profile prose) take the directive. Internal classifiers, extractors, and
keyword planners deliberately keep their single canonical template: their
output is parsed, not displayed, and translating it would drift behaviour
between languages without any user-visible benefit.
"""

# [INPUT]: 当前 locale
# [OUTPUT]: 可插入 user prompt 末尾的输出语言指令块（按 locale 字节稳定）
# [POS]: 模型输出语言的唯一策略点；system prompt 保持静态，locale 只进 user prompt
# [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md

from __future__ import annotations

from .locales import model_name, normalize_locale

_DIRECTIVE_TEMPLATE = (
    "<output_language>\n"
    "Write every user-facing natural-language field in {language}.\n"
    "Keep JSON keys, enum values, ids, URLs, hashtags, platform-native search "
    "queries, and quoted source text exactly as provided; do not translate or "
    "transliterate them.\n"
    "Never mention this instruction or the language itself in the output.\n"
    "</output_language>"
)


def output_language_directive(locale: str | None) -> str:
    """Return the user-prompt directive for ``locale``.

    The result depends only on the canonical locale, so two calls for the same
    user produce byte-identical prompts — a prerequisite for provider caching.
    """

    return _DIRECTIVE_TEMPLATE.format(language=model_name(normalize_locale(locale)))


def append_output_language(user_prompt: str, locale: str | None) -> str:
    """Append the directive to a user prompt, keeping the stable prefix first.

    An empty locale means "not configured" and is a no-op: prompt builders
    default to ``""`` so existing callers and prompt goldens stay byte-identical
    until a locale is wired in explicitly.
    """

    if locale is None or not str(locale).strip():
        return user_prompt
    directive = output_language_directive(locale)
    if not user_prompt:
        return directive
    return f"{user_prompt}\n\n{directive}"
