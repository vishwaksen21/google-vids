"""The validation gate: turns a stage's rule results into a stage status.

| Rule outcomes                         | Gate        | Next action                  |
|---------------------------------------|-------------|------------------------------|
| none at all                           | FAILED      | human (no evidence ≠ pass)   |
| every rule passed                     | PASSED      | proceed                      |
| only MINOR/INFO failures              | PASSED_WITH_WARNINGS | proceed             |
| any MAJOR failure                     | FAILED      | retry (bounded) then human   |
| any BLOCKER failure                   | FAILED      | human                        |

A rule that could not run (NOT_RUN) counts as a failure at its severity.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from vidsprod.domain.enums import Severity, StageStatus
from vidsprod.domain.validation import RuleResult


class GateAction(StrEnum):
    PROCEED = "PROCEED"
    RETRY = "RETRY"
    HUMAN = "HUMAN"


@dataclass(frozen=True)
class GateDecision:
    status: StageStatus
    action: GateAction
    reason: str
    failures: tuple[RuleResult, ...] = ()


def decide(results: list[RuleResult]) -> GateDecision:
    if not results:
        return GateDecision(StageStatus.FAILED, GateAction.HUMAN,
                            "the stage produced no validation results; no evidence is not a pass")
    failures = tuple(r for r in results if r.failed)
    blockers = [r for r in failures if r.severity is Severity.BLOCKER]
    majors = [r for r in failures if r.severity is Severity.MAJOR]
    if blockers:
        return GateDecision(StageStatus.FAILED, GateAction.HUMAN,
                            f"{len(blockers)} blocking finding(s): {blockers[0].message}", failures)
    if majors:
        return GateDecision(StageStatus.FAILED, GateAction.RETRY,
                            f"{len(majors)} major finding(s): {majors[0].message}", failures)
    if failures:
        return GateDecision(StageStatus.PASSED_WITH_WARNINGS, GateAction.PROCEED,
                            f"{len(failures)} warning(s)", failures)
    return GateDecision(StageStatus.PASSED, GateAction.PROCEED, "all checks passed")
