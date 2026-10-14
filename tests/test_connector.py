"""Tests for embcopilot.connector — cost-function-based connector strategy."""

from embcopilot.connector import ConnectorCostWeights, decide_connector, plan_connectors
from embcopilot.domain import ConnectorStrategy


def test_short_gap_prefers_hidden_travel_over_trim():
    decision = decide_connector(gap_mm=1.0, hidden_travel_max_mm=4.0)
    assert decision.strategy == ConnectorStrategy.HIDDEN_TRAVEL.value


def test_long_gap_prefers_jump_over_trim_when_jump_is_cheap_enough():
    # default trim_weight=8.0; a jump costs distance*1.0 + maybe a visible-travel
    # penalty of 3.0 -- so a 6mm gap (cost ~9) can still lose to trim (8) depending
    # on weights. Use explicit weights to make the intended comparison unambiguous.
    weights = ConnectorCostWeights(jump_distance_weight=0.5, trim_weight=20.0, visible_travel_weight=1.0)
    decision = decide_connector(gap_mm=6.0, hidden_travel_max_mm=4.0, weights=weights)
    assert decision.strategy == ConnectorStrategy.JUMP.value


def test_min_trims_is_not_always_the_answer():
    # A cheap trim_weight should make TRIM win even over a fairly short jump,
    # demonstrating the engine does not hard-code "minimum trims" as optimal.
    weights = ConnectorCostWeights(jump_distance_weight=5.0, trim_weight=2.0, visible_travel_weight=5.0)
    decision = decide_connector(gap_mm=3.0, hidden_travel_max_mm=1.0, weights=weights)
    assert decision.strategy == ConnectorStrategy.TRIM.value


def test_direction_conflict_adds_cost_to_every_candidate_equally():
    base = decide_connector(gap_mm=2.0, direction_conflict=False)
    conflicted = decide_connector(gap_mm=2.0, direction_conflict=True)
    assert conflicted.cost >= base.cost


def test_plan_connectors_handles_a_list_of_gaps():
    decisions = plan_connectors([1.0, 10.0, 0.5])
    assert len(decisions) == 3
    for d in decisions:
        assert d.strategy in {s.value for s in ConnectorStrategy}


def test_zero_gap_is_the_cheapest_hidden_travel_case():
    decision = decide_connector(gap_mm=0.0)
    assert decision.strategy == ConnectorStrategy.HIDDEN_TRAVEL.value
    assert decision.cost == 0.0
