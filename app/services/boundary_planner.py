from __future__ import annotations

from app.models import ArrangementTransition, BoundaryPlan, ScoreSection


def _effective_breath_beats(transition: ArrangementTransition, pickup_beats_b: float) -> float:
    mode = transition.transition_mode or "auto"
    if mode == "off":
        return 0.0
    if transition.breath_beats is not None:
        return max(0.0, float(transition.breath_beats))
    if mode == "auto":
        return 1.0 if pickup_beats_b > 0 else 0.0
    return 0.0


def _effective_run_on_beats(transition: ArrangementTransition, pickup_beats_b: float) -> float:
    mode = transition.transition_mode or "auto"
    if mode == "off":
        return 0.0
    if mode == "manual":
        requested = float(transition.run_on_beats or 0.0)
        return max(0.0, min(requested, pickup_beats_b))
    return min(pickup_beats_b, pickup_beats_b)


def plan_boundary(
    *,
    section_a_id: str,
    section_b_id: str,
    time_signature: str,
    pickup_beats_b: float,
    transition: ArrangementTransition,
) -> BoundaryPlan:
    breath_beats_effective = _effective_breath_beats(transition, pickup_beats_b)
    run_on_beats_effective = _effective_run_on_beats(transition, pickup_beats_b)
    return BoundaryPlan(
        sectionA_id=section_a_id,
        sectionB_id=section_b_id,
        time_signature=time_signature,
        breath_beats_effective=breath_beats_effective,
        pickup_beats_B=pickup_beats_b,
        tail_reservation_beats=breath_beats_effective + pickup_beats_b,
        run_on_beats_effective=run_on_beats_effective,
    )


def build_boundary_plans(
    *,
    sections: list[ScoreSection],
    time_signature: str,
    transitions: list[ArrangementTransition],
) -> list[BoundaryPlan]:
    boundaries = max(0, len(sections) - 1)
    plans: list[BoundaryPlan] = []
    for idx in range(boundaries):
        transition = transitions[idx] if idx < len(transitions) else ArrangementTransition()
        section_a = sections[idx]
        section_b = sections[idx + 1]
        plans.append(
            plan_boundary(
                section_a_id=section_a.id,
                section_b_id=section_b.id,
                time_signature=time_signature,
                pickup_beats_b=section_b.anacrusis_beats,
                transition=transition,
            )
        )
    return plans
