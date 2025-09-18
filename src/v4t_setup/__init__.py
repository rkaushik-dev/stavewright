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
"""

from __future__ import annotations

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

__all__ = [
    "MANUFACTURER",
    "PRODUCT_NAME",
    "PRODUCT_VERSION",
    "UPGRADE_CODE",
    "Architecture",
    "Executor",
    "Finding",
    "Manifest",
    "Plan",
    "Severity",
    "Stager",
    "Step",
    "StepKind",
    "TerminalModel",
    "TerminalProfile",
    "V4TError",
    "__version__",
    "build_manifest",
    "build_plan",
    "diff_plans",
    "dry_run",
    "normalise",
    "open_bundle",
    "read_aip",
    "read_manifest",
    "reconcile",
    "run_preflight",
]
