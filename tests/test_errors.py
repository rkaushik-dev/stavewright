from __future__ import annotations

import inspect

import pytest

from v4t_setup import errors
from v4t_setup.errors import (
    AipParseError,
    AipTableMissingError,
    DirectoryCycleError,
    DirectoryResolutionError,
    JournalError,
    ManifestError,
    PlanCycleError,
    PlanError,
    RollbackError,
    SignatureError,
    V4TError,
)


def _all_error_classes() -> list[type[V4TError]]:
    found = []
    for _, obj in inspect.getmembers(errors, inspect.isclass):
        if issubclass(obj, V4TError):
            found.append(obj)
    return found


def test_str_without_context_is_code_then_message():
    err = V4TError("something went sideways")
    assert str(err) == "[V4T0000] something went sideways"


def test_str_with_context_sorts_keys():
    err = DirectoryResolutionError(
        "unknown parent directory",
        context={"parent": "Nowhere_Dir", "directory": "Quattro_Dir"},
    )
    assert str(err) == (
        "[DIR0001] unknown parent directory "
        "(directory='Quattro_Dir', parent='Nowhere_Dir')"
    )


def test_explicit_code_overrides_the_class_default():
    err = ManifestError("bad entry", code="MAN0099")
    assert err.code == "MAN0099"
    assert str(err).startswith("[MAN0099]")


def test_class_default_survives_when_no_code_given():
    assert SignatureError("no signature").code == "MAN0003"


def test_as_dict_round_trips_the_useful_fields():
    err = PlanCycleError("cycle in ordering rules", context={"length": 3})
    payload = err.as_dict()
    assert payload == {
        "code": "PLN0002",
        "message": "cycle in ordering rules",
        "type": "PlanCycleError",
        "context": {"length": 3},
    }


def test_context_is_copied_not_aliased():
    supplied = {"path": "APPDIR"}
    err = V4TError("boom", context=supplied)
    supplied["path"] = "mutated"
    assert err.context == {"path": "APPDIR"}


def test_context_defaults_to_empty_dict():
    assert V4TError("boom").context == {}


@pytest.mark.parametrize(
    ("child", "parent"),
    [
        (AipTableMissingError, AipParseError),
        (DirectoryCycleError, DirectoryResolutionError),
        (SignatureError, ManifestError),
        (PlanCycleError, PlanError),
        (JournalError, errors.ApplyError),
        (RollbackError, errors.ApplyError),
    ],
)
def test_specialised_errors_keep_their_family(child, parent):
    assert issubclass(child, parent)
    assert issubclass(child, V4TError)


def test_every_error_is_catchable_as_the_base():
    for cls in _all_error_classes():
        with pytest.raises(V4TError):
            raise cls("raised for the catch test")


def test_codes_are_unique_per_class():
    seen: dict[str, str] = {}
    for cls in _all_error_classes():
        if cls is V4TError:
            continue
        assert cls.code not in seen, f"{cls.__name__} reuses {seen[cls.code]}'s code"
        seen[cls.code] = cls.__name__


def test_codes_follow_the_prefix_and_digits_shape():
    for cls in _all_error_classes():
        code = cls.code
        assert len(code) == 7, cls.__name__
        assert code[:3].isalnum() and code[:3].isupper(), cls.__name__
        assert code[3:].isdigit(), cls.__name__


def test_all_exported_names_exist():
    for name in errors.__all__:
        assert hasattr(errors, name), name
