from app.models import ArrangementTransition, ScoreSection
from app.services.boundary_planner import build_boundary_plans


def _sections(pickup_b: float) -> list[ScoreSection]:
    return [
        ScoreSection(id="sec-a", label="Verse", lyrics="a", syllables=[]),
        ScoreSection(id="sec-b", label="Chorus", lyrics="b", syllables=[], anacrusis_beats=pickup_b),
    ]


def test_boundary_planner_auto_defaults_breath_to_one_when_pickup_exists():
    plans = build_boundary_plans(
        sections=_sections(1.5),
        time_signature="4/4",
        transitions=[ArrangementTransition(transition_mode="auto")],
    )

    assert len(plans) == 1
    plan = plans[0]
    assert plan.breath_beats_effective == 1.0
    assert plan.pickup_beats_B == 1.5
    assert plan.tail_reservation_beats == 2.5
    assert plan.run_on_beats_effective == 1.5


def test_boundary_planner_manual_run_on_is_clamped_to_pickup():
    plans = build_boundary_plans(
        sections=_sections(1.0),
        time_signature="4/4",
        transitions=[ArrangementTransition(transition_mode="manual", breath_beats=0.25, run_on_beats=3.0)],
    )

    plan = plans[0]
    assert plan.breath_beats_effective == 0.25
    assert plan.run_on_beats_effective == 1.0


def test_boundary_planner_off_mode_forces_zero_run_on():
    plans = build_boundary_plans(
        sections=_sections(2.0),
        time_signature="3/4",
        transitions=[ArrangementTransition(transition_mode="off", breath_beats=1.0, run_on_beats=0.5)],
    )

    plan = plans[0]
    assert plan.breath_beats_effective == 0.0
    assert plan.run_on_beats_effective == 0.0
    assert plan.tail_reservation_beats == 2.0


def test_boundary_planner_is_deterministic_for_same_inputs():
    sections = _sections(1.0)
    transitions = [ArrangementTransition(transition_mode="manual", breath_beats=0.5, run_on_beats=0.75)]

    first = build_boundary_plans(sections=sections, time_signature="4/4", transitions=transitions)
    second = build_boundary_plans(sections=sections, time_signature="4/4", transitions=transitions)

    assert [plan.model_dump() for plan in first] == [plan.model_dump() for plan in second]
