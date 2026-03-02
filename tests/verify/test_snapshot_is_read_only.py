"""A snapshot must refuse every write the target port has.

The gap this closes is a new method on :class:`TargetSystem`. Add one and the
in memory target grows an implementation, the Windows target grows one, and
the snapshot grows nothing, because it is a structural implementation of a
Protocol rather than a subclass. Then somebody reconciles against a snapshot,
the new method is missing, and it fails with an AttributeError from inside a
probe rather than with the message this class exists to give.

So the list of write methods is derived from the port, not typed out here.
"""

from __future__ import annotations

import inspect

import pytest

from v4t_setup.model.registry import DecodedValue, RegistryRoot, RegistryValueType
from v4t_setup.target.base import AssemblyIdentity, ShortcutSpec, TargetSystem
from v4t_setup.verify.snapshot import ReadOnlyTargetError, Snapshot

IDENTITY = AssemblyIdentity(
    name="Microsoft.VC80.CRT",
    version="8.0.50727.6195",
    public_key_token="1fc8b3b9a1e18e3b",
)
SHORTCUT = ShortcutSpec(name="V4T", directory="DesktopFolder", target=r"C:\V4T.exe")
VALUE = DecodedValue(RegistryValueType.STRING, "x")

# One plausible argument per parameter name the port uses. A method whose
# parameters are not all in here is one this test cannot call, and the test
# below says so rather than skipping it.
ARGUMENTS: dict[str, object] = {
    "path": r"C:\Sellenis\S-jet600\V4T.exe",
    "data": b"MZ",
    "root": RegistryRoot.LOCAL_MACHINE,
    "key": r"Software\Matricode\V4T",
    "name": "Version",
    "value": VALUE,
    "identity": IDENTITY,
    "source": r"C:\staging\thing",
    "inf_name": "ftdibus.inf",
    "revision": "PCDC-1.4",
    "spec": SHORTCUT,
    "arguments": ["/q"],
}

WRITE_PREFIXES = (
    "make_",
    "remove_",
    "write_",
    "create_",
    "register_",
    "unregister_",
    "install_",
    "stage_",
    "program_",
    "run_",
)


def write_methods() -> list[str]:
    return sorted(
        name
        for name in dir(TargetSystem)
        if not name.startswith("_") and name.startswith(WRITE_PREFIXES)
    )


def call(snapshot: Snapshot, name: str) -> None:
    method = getattr(snapshot, name)
    signature = inspect.signature(getattr(TargetSystem, name))
    arguments = [
        ARGUMENTS[parameter]
        for parameter in signature.parameters
        if parameter not in ("self", "cls")
    ]
    method(*arguments)


def test_the_port_has_write_methods_to_check():
    assert len(write_methods()) > 15


def test_every_write_method_has_arguments_this_test_can_supply():
    unknown: set[str] = set()
    for name in write_methods():
        signature = inspect.signature(getattr(TargetSystem, name))
        unknown.update(
            parameter
            for parameter in signature.parameters
            if parameter not in ("self", "cls") and parameter not in ARGUMENTS
        )
    assert unknown == set(), f"add these to ARGUMENTS: {sorted(unknown)}"


@pytest.mark.parametrize("name", write_methods())
def test_a_snapshot_refuses_it(name):
    with pytest.raises(ReadOnlyTargetError):
        call(Snapshot(), name)


@pytest.mark.parametrize("name", write_methods())
def test_the_refusal_says_what_a_snapshot_is(name):
    with pytest.raises(ReadOnlyTargetError, match="photograph of a terminal"):
        call(Snapshot(), name)


@pytest.mark.parametrize("name", write_methods())
def test_the_refusal_names_the_operation(name):
    with pytest.raises(ReadOnlyTargetError) as caught:
        call(Snapshot(), name)
    assert "a snapshot cannot " in str(caught.value)
    # The message says what was attempted in words, not the method name.
    attempted = str(caught.value).split("a snapshot cannot ", 1)[1].split(".", 1)[0]
    assert attempted
    assert "_" not in attempted


def test_a_snapshot_still_satisfies_the_port():
    # Structural, so nothing enforces it at import time.
    assert isinstance(Snapshot(), TargetSystem)


def test_reading_a_snapshot_is_not_refused():
    snapshot = Snapshot()
    assert snapshot.path_exists(r"C:\nothing") is False
    assert snapshot.file_digest(r"C:\nothing") is None
    assert snapshot.controller_revision() is None
