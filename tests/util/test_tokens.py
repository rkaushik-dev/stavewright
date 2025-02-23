from __future__ import annotations

import pytest

from v4t_setup.util.tokens import (
    TokenRecursionError,
    UnknownPropertyError,
    expand,
    expand_all,
    find_tokens,
    from_multi_sz,
    is_build_time,
    to_multi_sz,
)

# The property table as the shipped AIP defines it.
PROPS = {
    "Manufacturer": "Sellenis",
    "ProductName": "S-Jet 600",
    "ProductVersion": "1.0.0",
    "ProductLanguage": "1033",
    "APPDIR": r"C:\Sellenis\S-jet600",
    "ALLUSERS": "1",
}


def test_plain_property_substitution():
    assert expand("[ProductName]", PROPS) == "S-Jet 600"


def test_build_time_form_resolves_the_same_way():
    assert expand("[|ProductName]", PROPS) == "S-Jet 600"


def test_the_shipped_arpcomments_row():
    source = (
        "This installer database contains the logic and data required to "
        "install [|ProductName]."
    )
    assert expand(source, PROPS) == (
        "This installer database contains the logic and data required to "
        "install S-Jet 600."
    )


def test_the_shipped_findexe_title_row():
    source = "Select the installation package for [|ProductName]"
    assert expand(source, PROPS) == "Select the installation package for S-Jet 600"


def test_several_tokens_in_one_string():
    source = "[Manufacturer] [ProductName] [ProductVersion]"
    assert expand(source, PROPS) == "Sellenis S-Jet 600 1.0.0"


def test_undefined_property_becomes_empty_by_default():
    assert expand("prefix[NoSuchProperty]suffix", PROPS) == "prefixsuffix"


def test_undefined_property_raises_in_strict_mode():
    with pytest.raises(UnknownPropertyError):
        expand("[NoSuchProperty]", PROPS, strict=True)


def test_defined_property_is_fine_in_strict_mode():
    assert expand("[ProductName]", PROPS, strict=True) == "S-Jet 600"


def test_escape_puts_a_bracket_into_the_value():
    assert expand(r"[\[]ProductName[\]]", PROPS) == "[ProductName]"


def test_escape_of_an_ordinary_character():
    assert expand(r"a[\b]c", PROPS) == "abc"


def test_environment_variable_is_read_when_supplied():
    assert expand("[%WINDIR]", PROPS, environment={"WINDIR": r"C:\Windows"}) == r"C:\Windows"


def test_environment_is_empty_unless_the_caller_opts_in():
    assert expand("[%WINDIR]", PROPS) == ""


def test_unknown_environment_variable_becomes_empty():
    assert expand("[%NOPE]", PROPS, environment={"WINDIR": r"C:\Windows"}) == ""


def test_null_token_becomes_a_nul_character():
    assert expand("a[~]b", PROPS) == "a\0b"


def test_nested_property_is_expanded():
    props = dict(PROPS, INSTALLBANNER="[Manufacturer] [ProductName]")
    assert expand("[INSTALLBANNER]", props) == "Sellenis S-Jet 600"


def test_two_levels_of_nesting():
    props = dict(PROPS, LEVEL1="[LEVEL2]", LEVEL2="[ProductName]")
    assert expand("[LEVEL1]", props) == "S-Jet 600"


def test_self_referencing_property_is_caught():
    props = dict(PROPS, LOOP="[LOOP]")
    with pytest.raises(TokenRecursionError, match="did not settle"):
        expand("[LOOP]", props)


def test_mutual_recursion_is_caught():
    props = dict(PROPS, PING="[PONG]", PONG="[PING]")
    with pytest.raises(TokenRecursionError):
        expand("[PING]", props)


def test_max_depth_is_configurable():
    props = dict(PROPS, A="[B]", B="[C]", C="done")
    assert expand("[A]", props, max_depth=4) == "done"
    with pytest.raises(TokenRecursionError):
        expand("[A]", props, max_depth=2)


def test_text_without_tokens_is_returned_unchanged():
    assert expand("regsvr32.exe c1sizer.ocx", PROPS) == "regsvr32.exe c1sizer.ocx"


def test_brackets_that_are_not_property_names_are_left_alone():
    assert expand("[Version] [not a name] [1234]", PROPS) == " [not a name] [1234]"


def test_a_windows_path_survives_expansion():
    assert expand("[APPDIR]\\V4T.exe", PROPS) == r"C:\Sellenis\S-jet600\V4T.exe"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[ProductName]", {"ProductName"}),
        ("[|ProductName]", {"ProductName"}),
        ("[Manufacturer] [ProductName]", {"Manufacturer", "ProductName"}),
        ("[%WINDIR]", set()),
        (r"[\[]", set()),
        ("[~]", set()),
        ("no tokens here", set()),
        ("[ProductName][ProductName]", {"ProductName"}),
    ],
)
def test_find_tokens(text, expected):
    assert find_tokens(text) == expected


def test_find_tokens_ignores_things_that_are_not_names():
    assert find_tokens("[not a name] [1234] [ProductName]") == {"ProductName"}


@pytest.mark.parametrize(
    ("body", "expected"),
    [("|ProductName", True), ("ProductName", False), ("%WINDIR", False)],
)
def test_is_build_time(body, expected):
    assert is_build_time(body) is expected


def test_expand_all_keeps_keys():
    values = {"title": "[ProductName]", "vendor": "[Manufacturer]"}
    assert expand_all(values, PROPS) == {"title": "S-Jet 600", "vendor": "Sellenis"}


def test_multi_sz_round_trip():
    parts = [r"C:\Sellenis\S-jet600", r"C:\Sellenis\S-jet600\System"]
    joined = to_multi_sz(parts)
    assert joined == r"C:\Sellenis\S-jet600[~]C:\Sellenis\S-jet600\System"
    assert from_multi_sz(expand(joined, PROPS)) == parts


def test_from_multi_sz_of_empty_is_empty_list():
    assert from_multi_sz("") == []


def test_multi_sz_with_one_entry():
    assert from_multi_sz(expand(to_multi_sz(["only"]), PROPS)) == ["only"]
