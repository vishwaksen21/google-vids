"""Validation results, inspection findings, verdicts and reports."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vidsprod.domain.enums import RuleCategory, RuleOutcome, Severity, Verdict


class RuleResult(BaseModel):
    """One rule applied to one subject. The unit every gate decides on."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    category: RuleCategory
    severity: Severity
    outcome: RuleOutcome
    message: str
    judge: str = "deterministic"  # or "llm" / "human"
    evidence: list[str] = Field(default_factory=list, description="Artifact ids")
    subject: str | None = None  # e.g. "scene:S02" / "take:<uuid>" / "export:<uuid>"
    fix_hint: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    @property
    def failed(self) -> bool:
        # A rule that could not run is a failure, never a pass.
        return self.outcome is not RuleOutcome.PASS


def passed(rule_id: str, category: RuleCategory, message: str, **kw: Any) -> RuleResult:
    return RuleResult(
        rule_id=rule_id, category=category, severity=kw.pop("severity", Severity.INFO),
        outcome=RuleOutcome.PASS, message=message, **kw,
    )


def failed(
    rule_id: str, category: RuleCategory, severity: Severity, message: str, **kw: Any
) -> RuleResult:
    return RuleResult(
        rule_id=rule_id, category=category, severity=severity, outcome=RuleOutcome.FAIL,
        message=message, **kw,
    )


def not_run(
    rule_id: str, category: RuleCategory, severity: Severity, message: str, **kw: Any
) -> RuleResult:
    return RuleResult(
        rule_id=rule_id, category=category, severity=severity, outcome=RuleOutcome.NOT_RUN,
        message=message, **kw,
    )


# ------------------------------------------------------------------ LLM judge shapes
class JudgeFinding(BaseModel):
    """What a vision judge reports. Kept flat for structured output."""

    model_config = ConfigDict(extra="forbid")

    aspect: str = Field(
        description=(
            "one of: prompt_adherence, brand, visual_quality, continuity, text, artifacts, "
            "narrative, technical, storyline, logo, cta, disclosure, sync"
        )
    )
    severity: Severity
    correctable_by_edit: bool = Field(
        description="True if a text-instruction edit of this clip could fix it"
    )
    message: str
    frame_timestamps: list[float] = Field(default_factory=list)
    edit_instruction: str | None = Field(
        default=None, description="If correctable, the positive instruction that fixes it"
    )


class JudgeReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    findings: list[JudgeFinding] = Field(default_factory=list, max_length=30)


class SceneVerdictRecord(BaseModel):
    verdict: Verdict
    reasons: list[str]
    results: list[RuleResult]
    edit_instruction: str | None = None
