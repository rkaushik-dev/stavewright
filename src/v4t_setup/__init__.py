"""V4T-Setup, the commissioning toolkit for S-Jet 600 marking terminals.

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
    from v4t_setup import build_manifest, build_plan, normalise, read_aip
    from v4t_setup.config.profiles import read_profile

    project = normalise(read_aip(Path("setup.aip")))
    manifest = build_manifest(project, Path(".")).manifest
    profile = read_profile(Path("LINE-PC-04.toml"))
    plan = build_plan(manifest, profile, project.directories).plan

Nothing here talks to a real machine on import. The Windows surface lives
behind :mod:`v4t_setup.target`, and every code path in the toolkit is exercised
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
    from v4t_setup.__about__ import (
        MANUFACTURER,
        PRODUCT_NAME,
        PRODUCT_VERSION,
        UPGRADE_CODE,
        __version__,
    )
    from v4t_setup.aip.builder import build_manifest
    from v4t_setup.aip.normalize import normalise
    from v4t_setup.aip.reader import read_aip
    from v4t_setup.apply.executor import Executor, dry_run
    from v4t_setup.errors import V4TError
    from v4t_setup.finding import Finding, Severity
    from v4t_setup.model.manifest import Manifest, read_manifest
    from v4t_setup.model.profile import Architecture, TerminalModel, TerminalProfile
    from v4t_setup.plan.diff import diff_plans
    from v4t_setup.plan.planner import build_plan
    from v4t_setup.plan.steps import Plan, Step, StepKind
    from v4t_setup.preflight.runner import run_preflight
    from v4t_setup.stage.stager import Stager, open_bundle
    from v4t_setup.verify.reconcile import reconcile

_EXPORTS: dict[str, str] = {
    "MANUFACTURER": "v4t_setup.__about__",
    "PRODUCT_NAME": "v4t_setup.__about__",
    "PRODUCT_VERSION": "v4t_setup.__about__",
    "UPGRADE_CODE": "v4t_setup.__about__",
    "__version__": "v4t_setup.__about__",
    "Architecture": "v4t_setup.model.profile",
    "Executor": "v4t_setup.apply.executor",
    "Finding": "v4t_setup.finding",
    "Manifest": "v4t_setup.model.manifest",
    "Plan": "v4t_setup.plan.steps",
    "Severity": "v4t_setup.finding",
    "Stager": "v4t_setup.stage.stager",
    "Step": "v4t_setup.plan.steps",
    "StepKind": "v4t_setup.plan.steps",
    "TerminalModel": "v4t_setup.model.profile",
    "TerminalProfile": "v4t_setup.model.profile",
    "V4TError": "v4t_setup.errors",
    "build_manifest": "v4t_setup.aip.builder",
    "build_plan": "v4t_setup.plan.planner",
    "diff_plans": "v4t_setup.plan.diff",
    "dry_run": "v4t_setup.apply.executor",
    "normalise": "v4t_setup.aip.normalize",
    "open_bundle": "v4t_setup.stage.stager",
    "read_aip": "v4t_setup.aip.reader",
    "read_manifest": "v4t_setup.model.manifest",
    "reconcile": "v4t_setup.verify.reconcile",
    "run_preflight": "v4t_setup.preflight.runner",
}


def __getattr__(name: str) -> Any:
    """Resolve one export, importing the module it lives in.

    :raises AttributeError: for anything not exported, which is what Python
        expects here and what makes ``from v4t_setup import nonsense`` fail
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
