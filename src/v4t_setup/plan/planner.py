"""Turning a manifest and a profile into an ordered plan.

Four things happen here and they happen in this order for a reason.

Selection first, because an item that does not apply to this terminal must not
influence the ordering of the ones that do. A Mini plan should not carry an
edge from the FTDI package to a bitstream that is not being installed.

Then edges, from the rules. Then the sort. Then resolution of each item to a
concrete path, last, because resolution can fail and it is more useful to fail
knowing which step you were on.

Steps come out in an order that is stable for a given manifest and profile.
Nothing in here iterates a set without sorting it.
"""

from __future__ import annotations

from dataclasses import dataclass

from v4t_setup.errors import PlanError
from v4t_setup.model.directory import DirectoryTable
from v4t_setup.model.manifest import Manifest
from v4t_setup.model.payload import PayloadItem, PayloadKind, rejected, select
from v4t_setup.model.profile import TerminalProfile
from v4t_setup.plan.graph import Graph
from v4t_setup.plan.rules import (
    DEFAULT_RULES,
    OrderingRule,
    collect_edges,
    unsatisfied_requirements,
)
from v4t_setup.plan.steps import Plan, Step, StepKind, utc_now

STEP_KIND_FOR: dict[PayloadKind, StepKind] = {
    PayloadKind.FILE: StepKind.COPY_FILE,
    PayloadKind.DIRECTORY: StepKind.CREATE_DIRECTORY,
    PayloadKind.REGISTRY: StepKind.WRITE_REGISTRY,
    PayloadKind.COM_SERVER: StepKind.REGISTER_COM,
    PayloadKind.SXS_ASSEMBLY: StepKind.INSTALL_SXS_ASSEMBLY,
    PayloadKind.SXS_POLICY: StepKind.INSTALL_SXS_POLICY,
    PayloadKind.DRIVER_PACKAGE: StepKind.STAGE_DRIVER,
    PayloadKind.FPGA_IMAGE: StepKind.PROGRAM_FPGA,
    PayloadKind.FONT: StepKind.INSTALL_FONT,
    PayloadKind.SHORTCUT: StepKind.CREATE_SHORTCUT,
    PayloadKind.TEMPLATE_SET: StepKind.INSTALL_TEMPLATE_SET,
    PayloadKind.LANGUAGE_PACK: StepKind.INSTALL_LANGUAGE_PACK,
    PayloadKind.REDISTRIBUTABLE: StepKind.RUN_REDISTRIBUTABLE,
}
"""One step kind per payload kind. Every kind is mapped, on purpose."""

KIND_PRIORITY: dict[PayloadKind, int] = {
    PayloadKind.REDISTRIBUTABLE: -60,
    PayloadKind.DIRECTORY: -50,
    PayloadKind.SXS_ASSEMBLY: -40,
    PayloadKind.SXS_POLICY: -35,
    PayloadKind.DRIVER_PACKAGE: -30,
    PayloadKind.FILE: -20,
    PayloadKind.FONT: -15,
    PayloadKind.LANGUAGE_PACK: -10,
    PayloadKind.COM_SERVER: -5,
    PayloadKind.TEMPLATE_SET: 0,
    PayloadKind.FPGA_IMAGE: 10,
    PayloadKind.SHORTCUT: 20,
    PayloadKind.REGISTRY: 30,
}
"""Tie break weights.

These do not create ordering, the rules do. They only decide what happens when
the graph genuinely does not care, so that a plan reads the way an engineer
would work rather than alphabetically.
"""


@dataclass
class PlanReport:
    """What the planner decided and why, beside the plan itself."""

    plan: Plan
    skipped: dict[str, str]
    missing_requirements: dict[str, list[str]]

    @property
    def has_warnings(self) -> bool:
        return bool(self.skipped or self.missing_requirements)

    def lines(self) -> list[str]:
        """Operator readable summary lines."""
        result = [self.plan.describe()]
        for item_id, reason in sorted(self.skipped.items()):
            result.append(f"skipped {item_id}: {reason}")
        for item_id, tags in sorted(self.missing_requirements.items()):
            result.append(f"{item_id} needs {', '.join(tags)} which nothing provides")
        return result


def _target_for(
    item: PayloadItem,
    profile: TerminalProfile,
    directories: DirectoryTable,
    native: bool,
) -> str | None:
    if not item.directory:
        return None
    resolved = directories.resolve(item.directory, profile, native=native)
    if item.kind is PayloadKind.DIRECTORY:
        return str(resolved)
    return str(resolved / item.name)


def _step_for(
    item: PayloadItem,
    profile: TerminalProfile,
    directories: DirectoryTable,
) -> Step:
    try:
        kind = STEP_KIND_FOR[item.kind]
    except KeyError:  # pragma: no cover - guarded by the completeness test
        raise PlanError(
            "no step kind for this payload kind",
            context={"item": item.item_id, "kind": item.kind.value},
        ) from None

    native = bool(item.detail.get("native", False))
    target = _target_for(item, profile, directories, native)
    return Step(
        step_id=item.item_id,
        kind=kind,
        title=item.describe(),
        component=item.component,
        target=target,
        source=str(item.source) if item.source else None,
        digest=item.digest,
        requires=item.requires,
        provides=item.provides,
        weight=KIND_PRIORITY.get(item.kind, 0),
        detail=dict(item.detail),
    )


def build_graph(
    items: list[PayloadItem],
    rules: tuple[OrderingRule, ...] = DEFAULT_RULES,
) -> Graph:
    """Build the ordering graph for *items*."""
    graph = Graph()
    for item in items:
        graph.add_node(item.item_id)
    known = {item.item_id for item in items}
    for before, after, reason in collect_edges(items, rules):
        if before in known and after in known:
            graph.add_edge(before, after, reason)
    return graph


def build_plan(
    manifest: Manifest,
    profile: TerminalProfile,
    directories: DirectoryTable,
    *,
    rules: tuple[OrderingRule, ...] = DEFAULT_RULES,
    stamp: bool = True,
) -> PlanReport:
    """Plan a commissioning run.

    :param stamp: set ``created_at``. Turn it off when comparing two plans byte
        for byte, since a timestamp would make them differ.
    :raises PlanError: when the manifest is unusable, or the rules produce a
        cycle, or an item names a directory the table cannot resolve.
    """
    manifest.validate()

    applicable = select(manifest.items, profile)
    skipped = rejected(manifest.items, profile)
    missing = unsatisfied_requirements(applicable)

    graph = build_graph(applicable, rules)
    by_id = {item.item_id: item for item in applicable}
    priority = {
        item_id: KIND_PRIORITY.get(item.kind, 0) for item_id, item in by_id.items()
    }
    order = graph.topological_order(priority=priority)

    steps = [_step_for(by_id[item_id], profile, directories) for item_id in order]

    plan = Plan(
        profile=profile,
        steps=steps,
        created_at=utc_now() if stamp else None,
        warnings=[
            *(f"skipped {item_id}: {reason}" for item_id, reason in sorted(skipped.items())),
            *(
                f"{item_id} requires {', '.join(tags)} which nothing in the bundle provides"
                for item_id, tags in sorted(missing.items())
            ),
        ],
    )
    return PlanReport(plan=plan, skipped=skipped, missing_requirements=missing)


def explain_order(plan: Plan, graph: Graph) -> list[str]:
    """One line per step saying what forced it to be where it is.

    A step with no predecessors is there because the tie break put it there,
    and saying so is more honest than leaving the line blank.
    """
    lines: list[str] = []
    for position, step in enumerate(plan, start=1):
        predecessors = graph.predecessors(step.step_id)
        if not predecessors:
            lines.append(f"{position:3d}. {step.step_id} (nothing requires it to be later)")
            continue
        reasons = [
            f"{source} ({graph.reason_for(source, step.step_id)})"
            for source in predecessors
        ]
        lines.append(f"{position:3d}. {step.step_id} after {'; '.join(reasons)}")
    return lines


__all__ = [
    "KIND_PRIORITY",
    "STEP_KIND_FOR",
    "PlanReport",
    "build_graph",
    "build_plan",
    "explain_order",
]
