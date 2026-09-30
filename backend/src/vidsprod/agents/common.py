"""Shared rules for the planning agents: approved facts, forbidden terms, claim detection."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from vidsprod.domain.enums import RuleCategory, Severity
from vidsprod.domain.validation import RuleResult, failed, passed

# Words that turn a sentence into a claim a regulator would read as a promise or a fact.
_CLAIM_CUES = re.compile(
    r"(\d|%|per ?cent|lakh|crore|rupee|₹|\bcashless\b|\bclaims?\b|\bsettle|\bhospitals?\b|"
    r"\bcover(?:s|age)? up to\b|\bpremium\b|\btax\b|\bno[- ]claim\b|\bwaiting period\b|"
    r"\brenewal\b|\bnetwork\b)",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\d[\d,.]*")


@dataclass
class AgentResult:
    document: dict[str, Any]
    results: list[RuleResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def feedback_block(previous: list[dict[str, Any]]) -> str:
    """Earlier attempts' failures, phrased as what to do, never quoting the rejected output."""
    if not previous:
        return ""
    lines = []
    for item in previous[:12]:
        hint = item.get("fix_hint") or item.get("message")
        if hint and hint not in lines:
            lines.append(hint)
    return "\n\nA previous attempt was rejected. This time, make sure that:\n" + "\n".join(
        f"- {line}" for line in lines
    )


def facts_by_id(profile_facts: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {f["fact_id"]: f for f in profile_facts}


def is_claim_like(text: str) -> bool:
    return bool(_CLAIM_CUES.search(text or ""))


def check_claims(
    *, rule_prefix: str, subject: str, text: str, fact_ids: list[str],
    facts: dict[str, dict[str, Any]],
) -> list[RuleResult]:
    results = []
    unknown = [f for f in fact_ids if f not in facts]
    if unknown:
        results.append(failed(
            f"{rule_prefix}.claims.unknown_fact", RuleCategory.BRAND_POLICY, Severity.BLOCKER,
            f"{subject} cites facts that are not approved: {', '.join(unknown)}",
            subject=subject, fix_hint="Cite only approved fact ids from the brand profile",
        ))
    if is_claim_like(text) and not fact_ids:
        results.append(failed(
            f"{rule_prefix}.claims.unsupported", RuleCategory.BRAND_POLICY, Severity.MAJOR,
            f'{subject} reads as a product claim but cites no approved fact: "{text[:80]}"',
            subject=subject,
            fix_hint="Remove figures and product claims that are not backed by an approved fact, "
                     "or cite the approved fact they come from",
        ))
    if fact_ids and not unknown:
        allowed_numbers = {n.strip(".,") for f in fact_ids for n in _NUMBER.findall(facts[f]["text"])}
        stray = [n.strip(".,") for n in _NUMBER.findall(text) if n.strip(".,") not in allowed_numbers]
        if stray:
            results.append(failed(
                f"{rule_prefix}.claims.number_not_in_fact", RuleCategory.BRAND_POLICY,
                Severity.MAJOR, f"{subject} uses figures {stray} that the cited facts do not contain",
                subject=subject, fix_hint="Use the figures exactly as the approved fact states them",
            ))
    return results


def check_forbidden(*, rule_id: str, subject: str, texts: Iterable[str],
                    forbidden: Iterable[str]) -> RuleResult:
    joined = " ".join(t for t in texts if t).lower()
    hits = sorted({term for term in forbidden
                   if re.search(rf"\b{re.escape(term.lower())}\b", joined)})
    if hits:
        return failed(rule_id, RuleCategory.BRAND_POLICY, Severity.MAJOR,
                      f"{subject} uses language the brand forbids: {', '.join(hits)}",
                      subject=subject,
                      fix_hint=f"Do not use these words or promises: {', '.join(hits)}")
    return passed(rule_id, RuleCategory.BRAND_POLICY, f"{subject}: no forbidden language",
                  subject=subject)


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
