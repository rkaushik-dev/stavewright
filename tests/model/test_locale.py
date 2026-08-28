from __future__ import annotations

import pytest

from stavewright.model.locale import (
    BASE_LANGUAGE_ID,
    UnknownLanguageError,
    all_languages,
    code_pages_for,
    components_for,
    needs_multiple_code_pages,
    resolve,
    resolve_all,
)

# The eight resource DLLs the AIP lists as components.
SHIPPED_COMPONENTS = {
    "LangChinese",
    "LangFrench",
    "LangGerman",
    "LangPolish",
    "LangPortuguese",
    "LangRussian",
    "LangSpanish",
    "LangTurkish",
}


def test_the_table_matches_the_components_in_the_aip():
    assert set(components_for(all_languages())) == SHIPPED_COMPONENTS


def test_english_is_the_base_language_and_has_no_component():
    english = resolve(BASE_LANGUAGE_ID)
    assert english.is_base
    assert english.component is None
    assert english.resource_file is None


def test_every_other_language_has_a_resource_dll():
    for entry in all_languages():
        if entry.is_base:
            continue
        assert entry.resource_file == f"{entry.component}.dll"


def test_lcids_are_unique():
    lcids = [entry.lcid for entry in all_languages()]
    assert len(set(lcids)) == len(lcids)


def test_tags_are_unique():
    tags = [entry.tag for entry in all_languages()]
    assert len(set(tags)) == len(tags)


@pytest.mark.parametrize(
    ("value", "expected_tag"),
    [
        (1049, "ru-RU"),
        ("1049", "ru-RU"),
        ("ru-RU", "ru-RU"),
        ("ru-ru", "ru-RU"),
        ("RU-RU", "ru-RU"),
        ("ru", "ru-RU"),
        ("LangRussian", "ru-RU"),
        (2052, "zh-CN"),
        ("zh", "zh-CN"),
        ("pt", "pt-BR"),
        ("  tr-TR  ", "tr-TR"),
    ],
)
def test_resolve_accepts_every_spelling(value, expected_tag):
    assert resolve(value).tag == expected_tag


@pytest.mark.parametrize("value", [9999, "9999", "kl-KL", "Klingon", "LangKlingon", ""])
def test_resolve_rejects_what_we_do_not_ship(value):
    with pytest.raises(UnknownLanguageError):
        resolve(value)


def test_unknown_lcid_says_so():
    with pytest.raises(UnknownLanguageError, match="no shipped language has LCID 9999"):
        resolve(9999)


def test_component_lookup_is_case_sensitive():
    assert resolve("LangPolish").tag == "pl-PL"
    with pytest.raises(UnknownLanguageError):
        resolve("langpolish")


def test_resolve_all_drops_duplicates():
    result = resolve_all([1049, "ru-RU", "LangRussian", "ru"])
    assert [entry.tag for entry in result] == ["ru-RU"]


def test_resolve_all_uses_table_order_not_caller_order():
    a = resolve_all(["tr-TR", "de-DE", "zh-CN"])
    b = resolve_all(["zh-CN", "tr-TR", "de-DE"])
    assert a == b
    assert [entry.tag for entry in a] == ["zh-CN", "de-DE", "tr-TR"]


def test_resolve_all_of_nothing_is_empty():
    assert resolve_all([]) == ()


def test_components_for_leaves_the_base_language_out():
    selected = resolve_all([BASE_LANGUAGE_ID, "pl-PL", "ru-RU"])
    assert components_for(selected) == ("LangPolish", "LangRussian")


def test_components_for_english_only_is_empty():
    assert components_for(resolve_all([BASE_LANGUAGE_ID])) == ()


@pytest.mark.parametrize(
    ("tag", "code_page"),
    [
        ("en-US", 1252),
        ("de-DE", 1252),
        ("fr-FR", 1252),
        ("es-ES", 1252),
        ("pt-BR", 1252),
        ("pl-PL", 1250),
        ("ru-RU", 1251),
        ("tr-TR", 1254),
        ("zh-CN", 936),
    ],
)
def test_code_pages(tag, code_page):
    assert resolve(tag).code_page == code_page


def test_western_languages_share_one_code_page():
    selected = resolve_all(["en-US", "de-DE", "fr-FR", "es-ES", "pt-BR"])
    assert code_pages_for(selected) == frozenset({1252})
    assert not needs_multiple_code_pages(selected)


def test_russian_and_turkish_together_need_two_code_pages():
    selected = resolve_all(["ru-RU", "tr-TR"])
    assert code_pages_for(selected) == frozenset({1251, 1254})
    assert needs_multiple_code_pages(selected)


def test_polish_alone_is_fine():
    assert not needs_multiple_code_pages(resolve_all(["pl-PL"]))


def test_english_plus_polish_needs_two_because_1250_is_not_1252():
    assert needs_multiple_code_pages(resolve_all(["en-US", "pl-PL"]))


def test_no_languages_needs_no_code_pages():
    assert code_pages_for(()) == frozenset()
    assert not needs_multiple_code_pages(())
