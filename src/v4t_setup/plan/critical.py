"""Which steps decide how long a run takes.

The cost model adds up every step, which is the right number for a bench doing
one machine at a time and the wrong one for the question people actually ask:
what would have to get faster for the run to be shorter.

A commissioning plan is a graph, and most of it is independent. Copying forty
files does not have to wait for the bitstream. So the run is as long as its
longest chain of dependent steps, and everything off that chain has slack:
it could take twice as long and change nothing.

Two things this is for.

Knowing where to spend effort. The bitstream is four minutes and nothing waits
on it, so making it faster saves nothing. The side by side assemblies are
twelve seconds each and the OCX registration waits on them, so they are on the
path and they are what to look at.

Knowing what a parallel executor would buy. This toolkit applies steps in
order, one at a time, on purpose: a half applied terminal is easier to reason
about that way. The gap between the total and the critical path is what that
decision costs, and it is worth being able to quote it rather than guess.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import timedelta

from v4t_setup.errors import PlanCycleError
from v4t_setup.plan.cost import step_seconds
from v4t_setup.plan.graph import Graph
from v4t_setup.plan.steps import Plan, Step


@dataclass(frozen=True, slots=True)
class Timing:
    """When one step could start and finish if nothing waited unnecessarily."""

    step_id: str
    duration: timedelta
    earliest_start: timedelta
    latest_start: timedelta

    @property
    def slack(self) -> timedelta:
        """How much this step could be delayed without moving the finish."""
        return self.latest_start - self.earliest_start

    @property
    def is_critical(self) -> bool:
        return self.slack == timedelta()

    @property
    def earliest_finish(self) -> timedelta:
        return self.earliest_start + self.duration

    def describe(self) -> str:
        state = "critical" if self.is_critical else f"{int(self.slack.total_seconds())}s slack"
        return f"{self.step_id}: {int(self.duration.total_seconds())}s, {state}"


@dataclass(frozen=True, slots=True)
class CriticalPath:
    """The longest chain of dependent steps, and what it implies."""

    timings: dict[str, Timing] = field(default_factory=dict)
    path: tuple[str, ...] = ()
    total: timedelta = timedelta()
    """Every step added up. What this toolkit's executor actually takes."""

    span: timedelta = timedelta()
    """The critical path. What a perfectly parallel executor would take."""

    def __len__(self) -> int:
        return len(self.path)

    def __iter__(self) -> Iterator[str]:
        return iter(self.path)

    def __contains__(self, step_id: str) -> bool:
        return step_id in self.path

    @property
    def saving(self) -> timedelta:
        """What running in parallel would save. Never negative."""
        return max(self.total - self.span, timedelta())

    @property
    def parallel_share(self) -> float:
        """The critical path as a fraction of the total, or 1.0 when empty.

        Near 1.0 means the plan is a chain and parallelism would buy nothing.
        Near 0 means almost everything is independent.
        """
        if not self.total:
            return 1.0
        return self.span.total_seconds() / self.total.total_seconds()

    def slack_for(self, step_id: str) -> timedelta | None:
        timing = self.timings.get(step_id)
        return timing.slack if timing else None

    def describe(self) -> str:
        minutes = self.span.total_seconds() / 60
        whole = self.total.total_seconds() / 60
        return (
            f"{len(self.path)} steps on the critical path, {minutes:.0f} of "
            f"{whole:.0f} minutes"
        )


def _durations(plan: Plan) -> dict[str, timedelta]:
    return {step.step_id: timedelta(seconds=step_seconds(step)) for step in plan}


def graph_of(plan: Plan) -> Graph:
    """The dependency graph a plan implies, from its requires and provides.

    Built here rather than taken as an argument because the planner throws its
    graph away once the order is settled, and rebuilding it is cheap.
    """
    graph = Graph()
    provided: dict[str, list[Step]] = {}
    for step in plan:
        graph.add_node(step.step_id)
        for tag in step.provides:
            provided.setdefault(tag, []).append(step)
    for step in plan:
        for tag in step.requires:
            for earlier in provided.get(tag, []):
                if earlier.step_id != step.step_id:
                    graph.add_edge(earlier.step_id, step.step_id, reason=tag)
    return graph


def critical_path(plan: Plan, graph: Graph | None = None) -> CriticalPath:
    """Work out the longest chain of dependent steps.

    :raises PlanCycleError: when the graph has a cycle, because a plan with one
        has no longest path and the planner would have refused it anyway.
    """
    work = graph if graph is not None else graph_of(plan)
    durations = _durations(plan)
    order = work.topological_order()

    earliest: dict[str, timedelta] = {}
    for node in order:
        starts = [
            earliest[before] + durations.get(before, timedelta())
            for before in work.predecessors(node)
            if before in earliest
        ]
        earliest[node] = max(starts, default=timedelta())

    span = max(
        (earliest[node] + durations.get(node, timedelta()) for node in order),
        default=timedelta(),
    )

    latest: dict[str, timedelta] = {}
    for node in reversed(order):
        after = [latest[node_after] for node_after in work.successors(node) if node_after in latest]
        latest[node] = min(after, default=span) - durations.get(node, timedelta())

    timings = {
        node: Timing(
            step_id=node,
            duration=durations.get(node, timedelta()),
            earliest_start=earliest[node],
            latest_start=latest[node],
        )
        for node in order
    }
    return CriticalPath(
        timings=timings,
        path=tuple(_walk(work, timings, span)),
        total=sum(durations.values(), timedelta()),
        span=span,
    )


def _walk(graph: Graph, timings: dict[str, Timing], span: timedelta) -> list[str]:
    """The critical steps in the order they would run.

    Ties are broken by step id so the same plan always names the same path.
    Two chains of identical length are equally true and a report that picks a
    different one each run is a report nobody can diff.
    """
    critical = sorted(
        (timing for timing in timings.values() if timing.is_critical),
        key=lambda timing: (timing.earliest_start, timing.step_id),
    )
    if not critical or not span:
        return [timing.step_id for timing in critical]

    chain: list[str] = []
    current = next(
        (timing for timing in critical if timing.earliest_start == timedelta()), None
    )
    while current is not None:
        chain.append(current.step_id)
        following = [
            timings[node]
            for node in sorted(graph.successors(current.step_id))
            if timings[node].is_critical
            and timings[node].earliest_start == current.earliest_finish
        ]
        current = following[0] if following else None
    return chain


def bottlenecks(path: CriticalPath, limit: int = 5) -> list[Timing]:
    """The longest steps on the critical path, which is where to look first."""
    on_path = [path.timings[step_id] for step_id in path.path if step_id in path.timings]
    return sorted(on_path, key=lambda timing: -timing.duration.total_seconds())[:limit]


def check_no_cycle(plan: Plan) -> None:
    """Refuse a plan whose steps cannot be ordered.

    :raises PlanCycleError: naming the cycle.
    """
    cycle = graph_of(plan).find_cycle()
    if cycle:
        raise PlanCycleError(
            "these steps depend on each other in a loop",
            context={"cycle": cycle},
        )


__all__ = [
    "CriticalPath",
    "Timing",
    "bottlenecks",
    "check_no_cycle",
    "critical_path",
    "graph_of",
]
