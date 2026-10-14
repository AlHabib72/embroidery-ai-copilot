"""Tests for embcopilot.sequence — dependency-respecting sequencing."""

import random

import pytest

from embcopilot.domain import SequenceItem
from embcopilot.sequence import DependencyCycleError, propose_sequence


def test_never_reorders_a_dependency_after_its_dependent():
    items = [
        SequenceItem("outline1", "red", "outline", dependencies=["detail1"]),
        SequenceItem("detail1", "red", "detail", dependencies=["fill1"]),
        SequenceItem("fill1", "red", "base_fill", dependencies=["underlay1"]),
        SequenceItem("underlay1", "red", "underlay", dependencies=[]),
    ]
    random.seed(1)
    shuffled = items[:]
    random.shuffle(shuffled)
    order = [item.object_id for item in propose_sequence(shuffled)]
    assert order.index("underlay1") < order.index("fill1")
    assert order.index("fill1") < order.index("detail1")
    assert order.index("detail1") < order.index("outline1")


def test_default_stage_order_used_when_no_dependencies_given():
    items = [
        SequenceItem("outline1", "red", "outline"),
        SequenceItem("underlay1", "red", "underlay"),
        SequenceItem("detail1", "red", "detail"),
        SequenceItem("fill1", "red", "base_fill"),
    ]
    order = [item.object_id for item in propose_sequence(items)]
    assert order == ["underlay1", "fill1", "detail1", "outline1"]


def test_cycle_is_detected_not_silently_resolved():
    a = SequenceItem("a", "red", "detail", dependencies=["b"])
    b = SequenceItem("b", "red", "detail", dependencies=["a"])
    with pytest.raises(DependencyCycleError):
        propose_sequence([a, b])


def test_does_not_mutate_the_input_list():
    items = [SequenceItem("a", "red", "outline"), SequenceItem("b", "red", "underlay")]
    original_order = [i.object_id for i in items]
    propose_sequence(items)
    assert [i.object_id for i in items] == original_order


def test_unknown_stage_sorts_last_by_default():
    items = [
        SequenceItem("mystery", "red", "some_new_stage"),
        SequenceItem("underlay1", "red", "underlay"),
    ]
    order = [item.object_id for item in propose_sequence(items)]
    assert order == ["underlay1", "mystery"]
