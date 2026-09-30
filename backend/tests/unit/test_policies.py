"""Gate decisions, verdict derivation, the correction policy and steering."""

from __future__ import annotations

import pytest

from vidsprod.agents.inspection import SceneBudget, decide_correction, derive_verdict, steering
from vidsprod.domain.enums import CorrectionAction, RuleCategory, Severity, StageStatus, Verdict
from vidsprod.domain.validation import failed, not_run, passed
from vidsprod.workflow.gates import GateAction, decide

C = RuleCategory


def test_no_evidence_is_not_a_pass():
    d = decide([])
    assert d.status is StageStatus.FAILED and d.action is GateAction.HUMAN


def test_all_passed_proceeds():
    assert decide([passed("a", C.CONTENT, "ok")]).status is StageStatus.PASSED


def test_minor_failures_pass_with_warnings():
    d = decide([passed("a", C.CONTENT, "ok"), failed("b", C.CONTENT, Severity.MINOR, "meh")])
    assert d.status is StageStatus.PASSED_WITH_WARNINGS and d.action is GateAction.PROCEED


def test_major_failure_retries_and_blocker_needs_a_person():
    assert decide([failed("b", C.CONTENT, Severity.MAJOR, "bad")]).action is GateAction.RETRY
    assert decide([failed("b", C.CONTENT, Severity.MAJOR, "bad"),
                   failed("c", C.CONTENT, Severity.BLOCKER, "worse")]).action is GateAction.HUMAN


def test_a_rule_that_could_not_run_counts_as_failed():
    d = decide([not_run("x", C.TECHNICAL, Severity.MAJOR, "no evidence")])
    assert d.status is StageStatus.FAILED


def _budget(**kw):
    base = dict(takes_used=1, edits_used=0, max_takes=3, max_edits=2,
                generations_available=True, edit_verified=True)
    return SceneBudget(**{**base, **kw})


@pytest.mark.parametrize("verdict,budget,action", [
    (Verdict.PASS, _budget(), CorrectionAction.ACCEPT_AND_LOCK),
    (Verdict.PASS_WITH_WARNINGS, _budget(), CorrectionAction.ACCEPT_AND_LOCK),
    (Verdict.NEEDS_EDIT, _budget(), CorrectionAction.EDIT),
    (Verdict.NEEDS_EDIT, _budget(edit_verified=False), CorrectionAction.REGENERATE),
    (Verdict.NEEDS_EDIT, _budget(edits_used=2), CorrectionAction.REGENERATE),
    (Verdict.NEEDS_REGENERATION, _budget(), CorrectionAction.REGENERATE),
    (Verdict.NEEDS_REGENERATION, _budget(takes_used=3), CorrectionAction.HUMAN_REVIEW),
    (Verdict.NEEDS_REGENERATION, _budget(generations_available=False), CorrectionAction.HUMAN_REVIEW),
    (Verdict.NEEDS_EDIT, _budget(generations_available=False), CorrectionAction.HUMAN_REVIEW),
    (Verdict.HUMAN_REVIEW, _budget(), CorrectionAction.HUMAN_REVIEW),
])
def test_correction_policy(verdict, budget, action):
    assert decide_correction(verdict, budget).action is action


def test_verdicts_from_findings():
    fixable = failed("judge.visual_quality", C.CONTENT, Severity.MINOR, "cool grade",
                     data={"correctable": True}, fix_hint="Warm the grade")
    cosmetic = failed("judge.visual_quality", C.CONTENT, Severity.MINOR, "slightly soft",
                      data={"correctable": False})
    broken = failed("tech.black_frames", C.TECHNICAL, Severity.MAJOR, "black",
                    data={"correctable": False}, fix_hint="Keep it lit")
    later = not_run("tech.media_analysis", C.TECHNICAL, Severity.MINOR, "checked on export")
    assert derive_verdict([passed("a", C.CONTENT, "ok")])[0] is Verdict.PASS
    assert derive_verdict([fixable])[0] is Verdict.NEEDS_EDIT
    assert derive_verdict([fixable])[2] == "Warm the grade"
    assert derive_verdict([cosmetic])[0] is Verdict.PASS_WITH_WARNINGS
    assert derive_verdict([broken, fixable])[0] is Verdict.NEEDS_REGENERATION
    assert derive_verdict([later])[0] is Verdict.PASS, "deferred checks are not defects"
    assert derive_verdict([not_run("e", C.OPERATIONAL, Severity.BLOCKER, "no frames")])[0] \
        is Verdict.HUMAN_REVIEW


def test_steering_is_positive_bounded_and_never_quotes_the_rejected_take():
    results = [failed(f"r{i}", C.CONTENT, Severity.MAJOR, f"the take showed defect {i}",
                      fix_hint=f"Do thing {i}") for i in range(5)]
    lines = steering(results)
    assert lines == ["Do thing 0", "Do thing 1", "Do thing 2"]
    assert not any("showed" in x for x in lines)
