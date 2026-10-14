"""Tests for embcopilot.rules — threshold-to-Issue mapping and scoring."""

from embcopilot.config import AnalyzerConfig
from embcopilot.models import Analysis, Issue, Segment
from embcopilot import rules


def _analysis(**overrides) -> Analysis:
    a = Analysis()
    for k, v in overrides.items():
        setattr(a, k, v)
    return a


def test_estimated_trims_rule_does_not_fire_below_threshold():
    cfg = AnalyzerConfig()
    a = _analysis(estimated_trims=5, trims_theoretical=1)
    assert rules.rule_estimated_trims(a, cfg) is None


def test_estimated_trims_rule_fires_above_threshold():
    cfg = AnalyzerConfig(min_trims_to_flag=12, min_trim_cut_pct=25)
    a = _analysis(estimated_trims=20, trims_theoretical=5)  # 75% cut
    issue = rules.rule_estimated_trims(a, cfg)
    assert issue is not None
    assert issue.severity in ("critical", "high")
    assert "20" in issue.detail


def test_wasted_travel_rule_needs_enough_jumps_and_cut():
    cfg = AnalyzerConfig(min_jumps_to_flag=40, min_travel_cut_pct=20)
    a = _analysis(jumps=10, travel_actual_mm=1000, travel_theoretical_mm=100)
    assert rules.rule_wasted_travel(a, cfg) is None  # not enough jumps
    a2 = _analysis(jumps=50, travel_actual_mm=1000, travel_theoretical_mm=990)
    assert rules.rule_wasted_travel(a2, cfg) is None  # cut too small


def test_tiny_stitch_rule_fires_and_carries_bbox():
    cfg = AnalyzerConfig(max_tiny_stitches=20, tiny_stitch_mm=0.3)
    a = _analysis(tiny_stitches=49, tiny_stitch_bbox=(1.0, 2.0, 3.0, 4.0))
    issue = rules.rule_tiny_stitches(a, cfg)
    assert issue is not None
    assert issue.severity == "critical"
    assert issue.bbox == (1.0, 2.0, 3.0, 4.0)
    assert "may" in issue.why_it_matters or "risk" in issue.why_it_matters.lower()


def test_tiny_stitch_language_never_claims_certainty():
    # The original bug this guards against: overclaiming certainty, e.g.
    # "these stitches WILL cause thread breaks" instead of a hedged risk
    # statement. Phrases like "not a guarantee" are fine (they explicitly
    # hedge) so this checks for absolute-certainty phrasing, not any
    # occurrence of the word "guarantee".
    cfg = AnalyzerConfig(max_tiny_stitches=1)
    a = _analysis(tiny_stitches=5)
    issue = rules.rule_tiny_stitches(a, cfg)
    banned = ["will cause", "always leads to", "guaranteed to", "is a guarantee"]
    text = (issue.detail + issue.why_it_matters).lower()
    for phrase in banned:
        assert phrase not in text
    assert "risk" in text or "may" in text or "can mean" in text


def test_short_stitch_rule_suppressed_when_tiny_already_fired():
    cfg = AnalyzerConfig(max_tiny_stitches=1, max_short_stitches=1)
    a = _analysis(tiny_stitches=5, short_stitches=10)
    # tiny already covers this cluster; short rule should not double-report.
    assert rules.rule_short_stitches(a, cfg) is None


def test_long_stitch_rule_severity_depends_on_satin_ceiling():
    cfg = AnalyzerConfig(long_stitch_mm=10.0, max_satin_mm=12.0)
    under_ceiling = _analysis(long_stitches=2, stitch_length_max_mm=11.0)
    over_ceiling = _analysis(long_stitches=2, stitch_length_max_mm=13.0)
    assert rules.rule_long_stitches(under_ceiling, cfg).severity == "high"
    assert rules.rule_long_stitches(over_ceiling, cfg).severity == "critical"


def test_long_stitch_rule_never_assumes_satin():
    cfg = AnalyzerConfig()
    a = _analysis(long_stitches=3, stitch_length_max_mm=11.0)
    issue = rules.rule_long_stitches(a, cfg)
    assert "satin" not in issue.title.lower()


def test_density_critical_suppresses_density_warning():
    cfg = AnalyzerConfig(max_dense_cells=5)
    a = _analysis(critical_cells=2, dense_cells=50)
    assert rules.rule_density_warning(a, cfg) is None
    assert rules.rule_density_critical(a, cfg) is not None


def test_fragmented_segments_rule():
    cfg = AnalyzerConfig(max_small_segment_stitches=12, max_small_segments=2)
    small = [
        Segment(0, 3, (0, 0), (1, 1), 1.0, (0, 0, 1, 1)),
        Segment(0, 3, (2, 2), (3, 3), 1.0, (2, 2, 3, 3)),
        Segment(0, 3, (4, 4), (5, 5), 1.0, (4, 4, 5, 5)),
    ]
    a = _analysis(segments=small)
    issue = rules.rule_fragmented_segments(a, cfg)
    assert issue is not None
    assert issue.count == 3


def test_heavy_stitch_count_rule():
    cfg = AnalyzerConfig(max_stitches_per_cm2=900)
    a = _analysis(width_mm=10, height_mm=10, stitches=2000)  # 1cm^2, 2000/cm2
    assert rules.rule_heavy_stitch_count(a, cfg) is not None
    a2 = _analysis(width_mm=10, height_mm=10, stitches=500)
    assert rules.rule_heavy_stitch_count(a2, cfg) is None


def test_score_starts_at_100_with_no_issues():
    value, grade = rules.score([])
    assert value == 100
    assert grade == "Production ready"


def test_score_never_negative():
    many = [Issue("critical", "x", "x", "x") for _ in range(20)]
    value, grade = rules.score(many)
    assert value == 0
    assert grade == "Rework"


def test_score_grade_bands():
    def issues_for(penalty_total):
        # each "medium" costs 4; build up to a target penalty
        n = penalty_total // 4
        return [Issue("medium", "x", "x", "x") for _ in range(n)]

    v, g = rules.score(issues_for(0))
    assert g == "Production ready"
    v, g = rules.score(issues_for(20))
    assert g == "Good"
    v, g = rules.score(issues_for(40))
    assert g == "Needs work"
    v, g = rules.score(issues_for(60))
    assert g == "Rework"


def test_severity_counts():
    issues = [
        Issue("critical", "a", "a", "a"),
        Issue("critical", "b", "b", "b"),
        Issue("high", "c", "c", "c"),
        Issue("low", "d", "d", "d"),
    ]
    counts = rules.severity_counts(issues)
    assert counts == {"critical": 2, "high": 1, "medium": 0, "low": 1}


def test_evaluate_sorts_most_severe_first():
    cfg = AnalyzerConfig(max_tiny_stitches=0, max_stitches_per_cm2=1)
    a = _analysis(
        tiny_stitches=5,
        width_mm=10,
        height_mm=10,
        stitches=100,
    )
    issues = rules.evaluate(a, cfg)
    severities = [i.severity for i in issues]
    assert severities == sorted(severities, key=lambda s: rules._SEVERITY_ORDER[s])
