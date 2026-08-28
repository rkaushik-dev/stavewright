"""Stavewright, the commissioning toolkit for S-Jet 600 marking terminals.

A terminal leaves the factory as a bare industrial PC. Commissioning it means
putting an FPGA bitstream on the print head controller, staging an FTDI driver
package, planting two side by side runtime assemblies with their publisher
policies, registering a pair of OCX controls into both the native and the WOW64
hive, dropping the GS1 template set the customer signed off, and writing the
product registry block. The order matters and most of it is not reversible by
hand once a line is running.

This package models all of that as data, plans it as a graph, and applies it
through a journal so a half finished bench can be rolled back.

The short version of the flow::

    from pathlib import Path
    from stavewright import build_manifest, build_plan, normalise, read_aip
    from stavewright.config.profiles import read_profile

    project = normalise(read_aip(Path("setup.aip")))
    manifest = build_manifest(project, Path(".")).manifest
    profile = read_profile(Path("LINE-PC-04.toml"))
    plan = build_plan(manifest, profile, project.directories).plan

Nothing here talks to a real machine on import. The Windows surface lives
behind :mod:`stavewright.target`, and every code path in the toolkit is exercised
against the in memory implementation of it.

What is exported here is the flow a caller normally wants. Everything else is
reachable through its own module, and the module docstrings are where the
reasoning lives.

Those exports are resolved on first use rather than on import. Written the
obvious way this file imported forty eight modules, and it is the first thing
Python loads for any of them, so reading a GS1 data file loaded the apply
executor and the compound file reader on the way. The cost of that is one
attribute lookup the first time, and a name that does not exist now fails when
it is used rather than when the package is imported.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from stavewright.__about__ import (
        MANUFACTURER,
        PRODUCT_NAME,
        PRODUCT_VERSION,
        UPGRADE_CODE,
        __version__,
    )
    from stavewright.aip.builder import build_manifest
    from stavewright.aip.normalize import normalise
    from stavewright.aip.reader import read_aip
    from stavewright.apply.executor import Executor, dry_run
    from stavewright.errors import StavewrightError
    from stavewright.finding import Finding, Severity
    from stavewright.model.manifest import Manifest, read_manifest
    from stavewright.model.profile import Architecture, TerminalModel, TerminalProfile
    from stavewright.plan.diff import diff_plans
    from stavewright.plan.planner import build_plan
    from stavewright.plan.steps import Plan, Step, StepKind
    from stavewright.preflight.runner import run_preflight
    from stavewright.stage.stager import Stager, open_bundle
    from stavewright.verify.reconcile import reconcile

_EXPORTS: dict[str, str] = {
    "MANUFACTURER": "stavewright.__about__",
    "PRODUCT_NAME": "stavewright.__about__",
    "PRODUCT_VERSION": "stavewright.__about__",
    "UPGRADE_CODE": "stavewright.__about__",
    "__version__": "stavewright.__about__",
    "Architecture": "stavewright.model.profile",
    "Executor": "stavewright.apply.executor",
    "Finding": "stavewright.finding",
    "Manifest": "stavewright.model.manifest",
    "Plan": "stavewright.plan.steps",
    "Severity": "stavewright.finding",
    "Stager": "stavewright.stage.stager",
    "Step": "stavewright.plan.steps",
    "StepKind": "stavewright.plan.steps",
    "TerminalModel": "stavewright.model.profile",
    "TerminalProfile": "stavewright.model.profile",
    "StavewrightError": "stavewright.errors",
    "build_manifest": "stavewright.aip.builder",
    "build_plan": "stavewright.plan.planner",
    "diff_plans": "stavewright.plan.diff",
    "dry_run": "stavewright.apply.executor",
    "normalise": "stavewright.aip.normalize",
    "open_bundle": "stavewright.stage.stager",
    "read_aip": "stavewright.aip.reader",
    "read_manifest": "stavewright.model.manifest",
    "reconcile": "stavewright.verify.reconcile",
    "run_preflight": "stavewright.preflight.runner",
}


def __getattr__(name: str) -> Any:
    """Resolve one export, importing the module it lives in.

    :raises AttributeError: for anything not exported, which is what Python
        expects here and what makes ``from stavewright import nonsense`` fail
        with the message a reader would want.
    """
    where = _EXPORTS.get(name)
    if where is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(where), name)
    globals()[name] = value
    return value


def _export_order(name: str) -> tuple[int, str]:
    """Constants, then classes, then functions, each alphabetical.

    The convention ruff's RUF022 enforces on every hand written ``__all__`` in
    this package. This one is computed, so it has to sort itself the same way
    or the package is the only module in the toolkit that reads differently.
    """
    if name.isupper():
        return (0, name)
    if name[0].isupper():
        return (1, name)
    return (2, name)


def __dir__() -> list[str]:
    return list(__all__)


__all__ = sorted(_EXPORTS, key=_export_order)
