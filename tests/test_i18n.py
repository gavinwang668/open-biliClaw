"""Unit tests for the locale contract, catalogs, and output-language policy."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.i18n import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    Translator,
    append_output_language,
    base_language,
    get_translator,
    html_lang,
    locale_chain,
    locale_options,
    model_name,
    native_name,
    normalize_locale,
    output_language_directive,
    resolve_locale,
)
from openbiliclaw.i18n.catalog import (
    CATALOG_DIR,
    UI_CATALOG_DIR,
    load_messages,
    translate,
)

if TYPE_CHECKING:
    from pathlib import Path

CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


class TestLocaleContract:
    def test_legacy_config_values_resolve_to_canonical_ids(self) -> None:
        assert normalize_locale("zh") == "zh-Hans"
        assert normalize_locale("en") == "en"
        assert normalize_locale("en-US") == "en"
        assert normalize_locale("zh-TW") == "zh-Hant"
        assert normalize_locale("zh_HK") == "zh-Hant"
        assert normalize_locale("zh-Hant-CN") == "zh-Hant"

    def test_unknown_values_fall_back_without_raising(self) -> None:
        assert normalize_locale("fr") == DEFAULT_LOCALE
        assert normalize_locale("") == DEFAULT_LOCALE
        assert normalize_locale(None) == DEFAULT_LOCALE
        assert resolve_locale("fr") is None
        assert resolve_locale("en-GB") == "en"

    def test_supported_locales_are_stable(self) -> None:
        assert SUPPORTED_LOCALES == ("zh-Hans", "zh-Hant", "en")
        assert DEFAULT_LOCALE in SUPPORTED_LOCALES

    def test_locale_chain_prefers_specific_then_source(self) -> None:
        assert locale_chain("zh-Hant") == ("zh-Hant", "zh-Hans", "en")
        assert locale_chain("en") == ("en", "zh-Hans")
        assert locale_chain("zh") == ("zh-Hans", "en")

    def test_display_helpers(self) -> None:
        assert base_language("zh-Hant") == "zh"
        assert html_lang("zh_TW") == "zh-Hant"
        assert native_name("zh-Hant") == "繁體中文"
        assert native_name("en") == "English"
        assert "Traditional Chinese" in model_name("zh-TW")
        assert [option.id for option in locale_options()] == list(SUPPORTED_LOCALES)
        assert locale_options()[0].as_dict()["native_name"] == "简体中文"


class TestConfigLanguageValidation:
    def test_config_writes_accept_aliases_and_reject_unknown_locales(self) -> None:
        from fastapi import HTTPException

        from openbiliclaw.api.app import _validated_config_language

        assert _validated_config_language("zh-TW") == "zh-Hant"
        assert _validated_config_language("en-US") == "en"
        assert _validated_config_language("zh") == "zh-Hans"
        with pytest.raises(HTTPException) as excinfo:
            _validated_config_language("fr")
        assert excinfo.value.status_code == 400
        detail = excinfo.value.detail
        assert isinstance(detail, dict)
        assert detail["error"] == "unsupported_language"
        assert "zh-Hant" in detail["supported"]


class TestSemanticCatalog:
    def test_translator_interpolates_and_falls_back(self) -> None:
        translator = get_translator("en")
        assert translator("cli.language.current", locale="en", native="English") == (
            "Current language: en (English)"
        )
        # Unknown key returns the key itself so the gap is visible.
        assert translator("cli.missing.key") == "cli.missing.key"

    def test_translator_falls_back_to_source_for_partial_locales(self) -> None:
        translator = Translator("zh-Hant")
        assert translator.has("cli.config_show.title")
        assert translator("cli.config_show.title")

    def test_module_level_translate_uses_locale_argument(self) -> None:
        assert translate("cli.config_show.title", locale="en") == "Current configuration"
        assert translate("cli.config_show.title", locale="zh-Hans") == "当前配置概览"

    @pytest.mark.parametrize("locale", ["zh-Hant", "en"])
    def test_semantic_catalogs_cover_every_source_key(self, locale: str) -> None:
        source = load_messages(DEFAULT_LOCALE)
        translated = load_messages(locale)
        missing = sorted(set(source) - set(translated))
        assert not missing, f"{locale} is missing semantic keys: {missing[:10]}"

    @pytest.mark.parametrize("locale", ["zh-Hant", "en"])
    def test_semantic_catalog_placeholders_match_source(self, locale: str) -> None:
        source = load_messages(DEFAULT_LOCALE)
        translated = load_messages(locale)
        for key, value in source.items():
            expected = set(re.findall(r"\{(\w+)\}", value))
            actual = set(re.findall(r"\{(\w+)\}", translated[key]))
            assert expected == actual, f"{locale}:{key} placeholder mismatch"

    def test_english_semantic_values_are_translated(self) -> None:
        english = load_messages("en")
        untranslated = [key for key, value in english.items() if CJK.search(value)]
        assert not untranslated, f"English catalog still contains Chinese: {untranslated[:10]}"

    def test_traditional_semantic_values_are_converted(self) -> None:
        traditional = load_messages("zh-Hant")
        # 当前配置概览 must come out as 當前配置概覽 in Traditional Chinese.
        assert traditional["cli.config_show.title"] == "當前配置概覽"
        assert traditional["cli.config_show.row.language"] == "語言"


class TestUiCatalogs:
    def test_ui_catalog_files_exist_for_every_shipped_locale(self) -> None:
        for locale in SUPPORTED_LOCALES:
            assert (UI_CATALOG_DIR / f"{locale}.json").is_file(), locale

    def test_traditional_ui_catalog_covers_the_source_inventory(self) -> None:
        source = _ui_messages("zh-Hans")
        traditional = _ui_messages("zh-Hant")
        missing = sorted(set(source) - set(traditional))
        assert not missing, f"zh-Hant missing UI strings: {missing[:10]}"
        assert not [key for key, value in traditional.items() if not value.strip()]

    def test_english_ui_catalog_covers_the_whole_inventory(self) -> None:
        source = _ui_messages("zh-Hans")
        english = _ui_messages("en")
        # Every English entry must still correspond to a rendered source string;
        # dead keys mean the extractor and the catalog drifted apart.
        unknown = sorted(set(english) - set(source))
        assert not unknown, f"English UI catalog has stale keys: {unknown[:10]}"
        # New Chinese copy must ship with English: run
        #   python scripts/build_i18n_catalogs.py --report
        # and add the missing entries to ui/en.json.
        missing = sorted(set(source) - set(english))
        assert not missing, f"English UI catalog is missing {len(missing)} strings: {missing[:10]}"
        assert not [
            key for key, value in english.items() if not value.strip() or CJK.search(value)
        ], "English UI catalog contains empty or still-Chinese values"

    def test_ui_inventory_is_not_empty(self) -> None:
        assert len(_ui_messages("zh-Hans")) > 1000


class TestCatalogGenerator:
    def test_traditional_catalogs_match_the_opencc_generator(self) -> None:
        opencc = pytest.importorskip("opencc")
        converter = opencc.OpenCC("s2twp")
        source = _ui_messages("zh-Hans")
        traditional = _ui_messages("zh-Hant")
        drifted = [text for text in source if traditional.get(text) != converter.convert(text)]
        assert not drifted, (
            f"zh-Hant UI catalog is stale; run python scripts/build_i18n_catalogs.py: {drifted[:5]}"
        )


class TestOutputLanguagePolicy:
    def test_directive_is_locale_specific_but_stable(self) -> None:
        assert output_language_directive("zh-Hant") == output_language_directive("zh-TW")
        assert "Traditional Chinese" in output_language_directive("zh-TW")
        assert "English" in output_language_directive("en")
        assert "Simplified Chinese" in output_language_directive("zh")

    def test_append_keeps_user_text_first(self) -> None:
        prompt = append_output_language("hello", "en")
        assert prompt.startswith("hello\n\n<output_language>")
        assert append_output_language("", "en").startswith("<output_language>")

    def test_empty_locale_defaults_to_simplified_chinese(self) -> None:
        assert "Simplified Chinese" in output_language_directive("")

    def test_append_is_a_noop_when_locale_is_unset(self) -> None:
        assert append_output_language("hello", "") == "hello"
        assert append_output_language("hello", None) == "hello"


class TestCatalogFilesAreCanonicalJson:
    @pytest.mark.parametrize("directory", [CATALOG_DIR, UI_CATALOG_DIR])
    def test_catalogs_are_sorted_and_utf8(self, directory: Path) -> None:
        for path in sorted(directory.glob("*.json")):
            raw = path.read_text(encoding="utf-8")
            payload = json.loads(raw)
            messages = payload["messages"]
            assert list(messages) == sorted(messages), f"{path} keys are not sorted"


def _ui_messages(locale: str) -> dict[str, str]:
    payload = json.loads((UI_CATALOG_DIR / f"{locale}.json").read_text(encoding="utf-8"))
    return {str(key): str(value) for key, value in payload["messages"].items()}
