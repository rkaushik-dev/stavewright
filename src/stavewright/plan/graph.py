"""A small directed graph with a stable topological order.

Stability is the whole point. Two engineers planning the same bundle for the
same terminal have to get byte identical plans, otherwise the plan digest moves
and the staging tree is rebuilt for no reason. Python's set iteration order is
not stable across processes, so every place this module could iterate a set it
sorts instead.

When the graph does not sort, the error carries the actual cycle rather than
just saying one exists. Being told that a 60 step plan has a cycle in it
somewhere is not useful at two in the morning.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from stavewright.errors import PlanCycleError, PlanError


@dataclass
class Graph:
    """Nodes identified by string, with edges meaning "must come before"."""

    nodes: set[str] = field(default_factory=set)
    edges: dict[str, set[str]] = field(default_factory=dict)
    reasons: dict[tuple[str, str], str] = field(default_factory=dict)

    def add_node(self, node: str) -> None:
        """Add a node. Adding one twice is not an error."""
        if not node:
            raise PlanError("graph node identifier must not be empty")
        self.nodes.add(node)
        self.edges.setdefault(node, set())

    def add_edge(self, before: str, after: str, reason: str = "") -> None:
        """Require *before* to run before *after*.

        Both nodes are created if they are not already present, because a rule
        that names a step which is not in this bundle is usually a rule that
        should have been filtered out earlier, and failing here would hide that.

        :param reason: recorded so a plan dump can explain the ordering.
        :raises PlanError: on a self edge, which is always a rule bug.
        """
        if before == after:
            raise PlanError("step cannot depend on itself", context={"step": before})
        self.add_node(before)
        self.add_node(after)
        self.edges[before].add(after)
        if reason:
            self.reasons[(before, after)] = reason

    def successors(self, node: str) -> list[str]:
        """Nodes that must come after *node*, sorted."""
        return sorted(self.edges.get(node, set()))

    def predecessors(self, node: str) -> list[str]:
        """Nodes that must come before *node*, sorted."""
        return sorted(source for source, targets in self.edges.items() if node in targets)

    def indegree(self) -> dict[str, int]:
        """How many predecessors each node has."""
        counts = dict.fromkeys(self.nodes, 0)
        for targets in self.edges.values():
            for target in targets:
                counts[target] = counts.get(target, 0) + 1
        return counts

    def reason_for(self, before: str, after: str) -> str:
        """Why this edge exists, or an empty string when nothing was recorded."""
        return self.reasons.get((before, after), "")

    def __len__(self) -> int:
        return len(self.nodes)

    def __contains__(self, node: str) -> bool:
        return node in self.nodes

    @property
    def edge_count(self) -> int:
        return sum(len(targets) for targets in self.edges.values())

    def find_cycle(self) -> list[str] | None:
        """Return one cycle as a node path, or None when the graph is acyclic.

        The path is closed, so ``["a", "b", "a"]`` reads the way an operator
        would say it out loud.
        """
        white = set(self.nodes)
        grey: set[str] = set()
        stack: list[str] = []

        def walk(node: str) -> list[str] | None:
            white.discard(node)
            grey.add(node)
            stack.append(node)
            for target in sorted(self.edges.get(node, set())):
                if target in grey:
                    start = stack.index(target)
                    return [*stack[start:], target]
                if target in white:
                    found = walk(target)
                    if found is not None:
                        return found
            grey.discard(node)
            stack.pop()
            return None

        for node in sorted(self.nodes):
            if node in white:
                found = walk(node)
                if found is not None:
                    return found
        return None

    def topological_order(self, priority: dict[str, int] | None = None) -> list[str]:
        """Sort the graph.

        Ties are broken by *priority* first and then by node name, so the order
        is the same on every machine and every run.

        :param priority: lower numbers go earlier. Missing nodes score zero.
        :raises PlanCycleError: when the graph does not sort, with the cycle in
            the error context.
        """
        weights = priority or {}
        remaining = self.indegree()
        ready = sorted(
            (node for node, count in remaining.items() if count == 0),
            key=lambda node: (weights.get(node, 0), node),
        )
        order: list[str] = []

        while ready:
            node = ready.pop(0)
            order.append(node)
            for target in sorted(self.edges.get(node, set())):
                remaining[target] -= 1
                if remaining[target] == 0:
                    ready.append(target)
            ready.sort(key=lambda node: (weights.get(node, 0), node))

        if len(order) != len(self.nodes):
            cycle = self.find_cycle() or sorted(set(self.nodes) - set(order))
            raise PlanCycleError(
                "ordering rules produced a cycle",
                context={"cycle": " -> ".join(cycle), "length": len(cycle)},
            )
        return order

    def levels(self) -> list[list[str]]:
        """Group nodes into waves that could run in parallel.

        Nothing runs a commissioning plan in parallel today. This exists so a
        plan dump can show which steps are ordered by a real dependency and
        which are only ordered because the sort had to pick something.

        :raises PlanCycleError: when the graph does not sort.
        """
        remaining = self.indegree()
        result: list[list[str]] = []
        placed = 0
        current = sorted(node for node, count in remaining.items() if count == 0)
        while current:
            result.append(current)
            placed += len(current)
            following: set[str] = set()
            for node in current:
                for target in self.edges.get(node, set()):
                    remaining[target] -= 1
                    if remaining[target] == 0:
                        following.add(target)
            current = sorted(following)
        if placed != len(self.nodes):
            cycle = self.find_cycle() or []
            raise PlanCycleError(
                "ordering rules produced a cycle",
                context={"cycle": " -> ".join(cycle), "length": len(cycle)},
            )
        return result

    def reachable_from(self, node: str) -> set[str]:
        """Every node that must run after *node*, transitively."""
        seen: set[str] = set()
        stack = [node]
        while stack:
            current = stack.pop()
            for target in self.edges.get(current, set()):
                if target not in seen:
                    seen.add(target)
                    stack.append(target)
        return seen

    def subgraph(self, keep: Iterable[str]) -> Graph:
        """A copy holding only *keep*, with edges to dropped nodes removed."""
        kept = set(keep)
        result = Graph()
        for node in sorted(kept & self.nodes):
            result.add_node(node)
        for source, targets in self.edges.items():
            if source not in kept:
                continue
            for target in sorted(targets):
                if target in kept:
                    result.add_edge(source, target, self.reason_for(source, target))
        return result


__all__ = ["Graph"]
