from __future__ import annotations

import pytest

from v4t_setup.errors import PlanCycleError, PlanError
from v4t_setup.plan.graph import Graph


def build(*edges: tuple[str, str]) -> Graph:
    graph = Graph()
    for before, after in edges:
        graph.add_edge(before, after)
    return graph


def test_an_empty_graph_sorts_to_nothing():
    assert Graph().topological_order() == []
    assert len(Graph()) == 0


def test_isolated_nodes_come_out_in_name_order():
    graph = Graph()
    for node in ["zulu", "alpha", "mike"]:
        graph.add_node(node)
    assert graph.topological_order() == ["alpha", "mike", "zulu"]


def test_a_chain_sorts_in_order():
    graph = build(("sxs", "crt"), ("crt", "ocx"), ("ocx", "exe"))
    assert graph.topological_order() == ["sxs", "crt", "ocx", "exe"]


def test_a_diamond_sorts_with_the_root_first_and_the_join_last():
    graph = build(("root", "left"), ("root", "right"), ("left", "join"), ("right", "join"))
    order = graph.topological_order()
    assert order[0] == "root"
    assert order[-1] == "join"
    assert set(order[1:3]) == {"left", "right"}


def test_ties_break_by_name_so_the_order_is_reproducible():
    graph = build(("root", "zulu"), ("root", "alpha"), ("root", "mike"))
    assert graph.topological_order() == ["root", "alpha", "mike", "zulu"]


def test_the_same_graph_built_in_a_different_order_sorts_the_same():
    forwards = build(("a", "b"), ("a", "c"), ("b", "d"), ("c", "d"))
    backwards = build(("c", "d"), ("b", "d"), ("a", "c"), ("a", "b"))
    assert forwards.topological_order() == backwards.topological_order()


def test_priority_beats_the_name_tiebreak():
    graph = build(("root", "zulu"), ("root", "alpha"))
    order = graph.topological_order(priority={"zulu": -1})
    assert order == ["root", "zulu", "alpha"]


def test_priority_never_beats_a_real_dependency():
    graph = build(("late", "early"))
    assert graph.topological_order(priority={"early": -100}) == ["late", "early"]


def test_a_missing_priority_scores_zero():
    graph = build(("root", "a"), ("root", "b"))
    assert graph.topological_order(priority={"b": -1}) == ["root", "b", "a"]


def test_add_node_is_idempotent():
    graph = Graph()
    graph.add_node("one")
    graph.add_node("one")
    assert len(graph) == 1


def test_an_empty_node_name_is_refused():
    with pytest.raises(PlanError, match="must not be empty"):
        Graph().add_node("")


def test_a_self_edge_is_refused():
    with pytest.raises(PlanError, match="cannot depend on itself"):
        Graph().add_edge("a", "a")


def test_adding_an_edge_creates_both_nodes():
    graph = Graph()
    graph.add_edge("a", "b")
    assert "a" in graph
    assert "b" in graph
    assert len(graph) == 2


def test_a_repeated_edge_is_counted_once():
    graph = Graph()
    graph.add_edge("a", "b")
    graph.add_edge("a", "b")
    assert graph.edge_count == 1


def test_successors_and_predecessors_are_sorted():
    graph = build(("root", "zulu"), ("root", "alpha"), ("alpha", "leaf"), ("zulu", "leaf"))
    assert graph.successors("root") == ["alpha", "zulu"]
    assert graph.predecessors("leaf") == ["alpha", "zulu"]
    assert graph.predecessors("root") == []
    assert graph.successors("leaf") == []


def test_successors_of_an_unknown_node_is_empty():
    assert Graph().successors("nope") == []


def test_indegree_counts_predecessors():
    graph = build(("a", "c"), ("b", "c"))
    assert graph.indegree() == {"a": 0, "b": 0, "c": 2}


def test_reasons_are_kept_with_their_edge():
    graph = Graph()
    graph.add_edge("sxs", "ocx", "the control binds the VC80 assembly")
    assert graph.reason_for("sxs", "ocx") == "the control binds the VC80 assembly"
    assert graph.reason_for("ocx", "sxs") == ""


def test_a_two_node_cycle_is_found():
    graph = build(("a", "b"), ("b", "a"))
    assert graph.find_cycle() == ["a", "b", "a"]


def test_a_longer_cycle_is_found():
    graph = build(("a", "b"), ("b", "c"), ("c", "a"))
    assert graph.find_cycle() == ["a", "b", "c", "a"]


def test_a_cycle_hanging_off_an_acyclic_head_is_found():
    graph = build(("start", "a"), ("a", "b"), ("b", "a"))
    cycle = graph.find_cycle()
    assert cycle is not None
    assert cycle[0] == cycle[-1]
    assert set(cycle) == {"a", "b"}


def test_an_acyclic_graph_has_no_cycle():
    assert build(("a", "b"), ("b", "c")).find_cycle() is None


def test_sorting_a_cyclic_graph_reports_the_cycle():
    graph = build(("sxs", "crt"), ("crt", "ocx"), ("ocx", "sxs"))
    with pytest.raises(PlanCycleError, match="produced a cycle") as excinfo:
        graph.topological_order()
    assert excinfo.value.context["cycle"] == "crt -> ocx -> sxs -> crt"
    assert excinfo.value.context["length"] == 4


def test_a_cycle_beside_a_sortable_part_still_raises():
    graph = build(("independent", "leaf"), ("a", "b"), ("b", "a"))
    with pytest.raises(PlanCycleError):
        graph.topological_order()


def test_levels_group_independent_work():
    graph = build(("root", "left"), ("root", "right"), ("left", "join"), ("right", "join"))
    assert graph.levels() == [["root"], ["left", "right"], ["join"]]


def test_levels_of_a_chain_is_one_node_each():
    assert build(("a", "b"), ("b", "c")).levels() == [["a"], ["b"], ["c"]]


def test_levels_of_isolated_nodes_is_a_single_wave():
    graph = Graph()
    for node in ["b", "a", "c"]:
        graph.add_node(node)
    assert graph.levels() == [["a", "b", "c"]]


def test_levels_of_an_empty_graph_is_empty():
    assert Graph().levels() == []


def test_levels_reports_a_cycle():
    with pytest.raises(PlanCycleError):
        build(("a", "b"), ("b", "a")).levels()


def test_reachable_from_follows_the_whole_tail():
    graph = build(("a", "b"), ("b", "c"), ("c", "d"), ("x", "y"))
    assert graph.reachable_from("a") == {"b", "c", "d"}
    assert graph.reachable_from("c") == {"d"}
    assert graph.reachable_from("d") == set()


def test_reachable_from_an_unknown_node_is_empty():
    assert Graph().reachable_from("nope") == set()


def test_subgraph_keeps_only_the_named_nodes():
    graph = build(("a", "b"), ("b", "c"), ("c", "d"))
    kept = graph.subgraph(["a", "b", "c"])
    assert len(kept) == 3
    assert kept.topological_order() == ["a", "b", "c"]


def test_subgraph_drops_edges_that_leave_the_selection():
    graph = build(("a", "b"), ("b", "c"))
    kept = graph.subgraph(["a", "b"])
    assert kept.edge_count == 1
    assert kept.successors("b") == []


def test_subgraph_carries_reasons_across():
    graph = Graph()
    graph.add_edge("a", "b", "because")
    assert graph.subgraph(["a", "b"]).reason_for("a", "b") == "because"


def test_subgraph_ignores_names_that_are_not_in_the_graph():
    graph = build(("a", "b"))
    assert len(graph.subgraph(["a", "b", "ghost"])) == 2


def test_a_realistic_commissioning_shape_sorts():
    graph = build(
        ("vcredist", "sxs.crt"),
        ("vcredist", "sxs.mfc"),
        ("sxs.crt", "sxs.policy.crt"),
        ("sxs.mfc", "sxs.policy.mfc"),
        ("sxs.policy.crt", "ocx.c1sizer"),
        ("sxs.policy.mfc", "ocx.vsflex"),
        ("ocx.c1sizer", "exe.v4t"),
        ("ocx.vsflex", "exe.v4t"),
        ("driver.ftdi", "fpga.bitstream"),
        ("exe.v4t", "registry.productinfo"),
        ("fpga.bitstream", "registry.productinfo"),
    )
    order = graph.topological_order()
    assert order.index("vcredist") < order.index("sxs.crt")
    assert order.index("sxs.policy.crt") < order.index("ocx.c1sizer")
    assert order.index("driver.ftdi") < order.index("fpga.bitstream")
    assert order[-1] == "registry.productinfo"
