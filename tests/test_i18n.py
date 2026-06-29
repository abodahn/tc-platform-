"""Trilingual i18n integrity: valid JSON, key parity across EN/AR/TR, no blanks."""
import json
from pathlib import Path

import pytest

I18N = Path(__file__).resolve().parent.parent / "app" / "static" / "i18n"
LANGS = ["en", "ar", "tr"]


def _load(lang):
    return json.loads((I18N / f"{lang}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("lang", LANGS)
def test_lang_file_is_valid_json_dict(lang):
    data = _load(lang)
    assert isinstance(data, dict) and data, f"{lang}.json must be a non-empty object"


@pytest.mark.parametrize("lang", ["ar", "tr"])
def test_translations_cover_all_english_keys(lang):
    en = set(_load("en"))
    other = set(_load(lang))
    missing = en - other
    assert not missing, f"{lang}.json is missing {len(missing)} key(s), e.g. {sorted(missing)[:8]}"


@pytest.mark.parametrize("lang", LANGS)
def test_no_blank_values(lang):
    blanks = [k for k, v in _load(lang).items() if not str(v).strip()]
    assert not blanks, f"{lang}.json has blank values: {blanks[:8]}"
