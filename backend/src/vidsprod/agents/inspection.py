"""Scene quality inspection and the correction decision.

Inspection has three layers, and every one produces `RuleResult`s:

1. **Technical, deterministic** — the clip exists, decodes, has the requested duration
   and aspect ratio, has no black or frozen stretches (ffmpeg's own detectors).
2. **Speech, deterministic** — the audio's transcript is compared with the approved words
   (ported `unexpected_speech`): nothing invented, nothing missing.
3. **Visual, LLM judge** — frames plus the scene spec, the brand slice and frames of the
   accepted scenes it must stay continuous with. Structured findings with severities and
   whether a text-instruction edit could fix each one.

The verdict is derived from the results by rule, not asked of the model; the correction
decision is a pure function of the verdict, the scene's history, the budget and the
verified capabilities.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vidsprod.config import Settings
from vidsprod.domain.enums import CorrectionAction, RuleCategory, Severity, Verdict
from vidsprod.domain.validation import JudgeReport, RuleResult, failed, not_run, passed
from vidsprod.llm.service import AIService
from vidsprod.llm.types import LLMRequest, MediaPart, ModelRole
from vidsprod.media import ffmpeg
from vidsprod.planning.dialogue import TRANSCRIBE_INSTRUCTION, unexpected_speech
from vidsprod.vids.capabilities import CapabilityRegistry
from vidsprod.vids.port import ClipEvidence

C = RuleCategory

# Positive steering per aspect, used when a finding cannot be fixed by an edit and the next
# take has to be steered instead.
_ASPECT_STEER = {
    "continuity": "Keep every character, their wardrobe and the setting exactly as described, "
                  "identical to the earlier scenes",
    "prompt_adherence": "Show exactly the action described, clearly and fully in frame",
    "text": "No text, captions, signage or logos anywhere in the picture",
    "artifacts": "Hands, faces and objects look natural, anatomically correct and undistorted",
    "visual_quality": "A clean, well-exposed, sharp picture",
    "brand": "Brand-controlled colour only from the brand palette",
    "narrative": "Make the scene's purpose obvious from the action",
    "technical": "A continuous, well-exposed shot for the full duration",
}
_EDGE_TOLERANCE = 0.25  # black at the very start/end of a clip is a fade, not a defect


@dataclass
class Inspection:
    results: list[RuleResult]
    verdict: Verdict
    reasons: list[str]
    edit_instruction: str | None


async def inspect_clip(
    *, ai: AIService, settings: Settings, registry: CapabilityRegistry, scene: dict[str, Any],
    prompt: dict[str, Any], evidence: ClipEvidence, brand_block: str,
    continuity_frames: list[tuple[str, Path]], job_id: Any, subject: str,
) -> Inspection:
    results: list[RuleResult] = []
    results += await _technical(settings, scene, prompt, evidence, subject)
    results += await _speech(ai, registry, prompt, evidence, subject, job_id)
    results += await _visual(ai, scene, prompt, evidence, brand_block, continuity_frames,
                             subject, job_id)
    verdict, reasons, instruction = derive_verdict(results)
    return Inspection(results, verdict, reasons, instruction)


async def _technical(settings: Settings, scene: dict, prompt: dict, ev: ClipEvidence,
                     subject: str) -> list[RuleResult]:
    out: list[RuleResult] = []
    visible = ev.frames or ev.screenshots
    if not visible:
        out.append(not_run("tech.evidence", C.OPERATIONAL, Severity.BLOCKER,
                           "no frames or screenshots of the clip could be captured",
                           subject=subject))
        return out
    out.append(passed("tech.evidence", C.OPERATIONAL,
                      f"{len(ev.frames)} frames, {len(ev.screenshots)} screenshots captured",
                      subject=subject))
    requested = float(prompt["duration_seconds"])
    if ev.duration_seconds is None:
        out.append(not_run("tech.duration", C.TECHNICAL, Severity.MINOR,
                           "clip duration could not be read (checked again on the export)",
                           subject=subject))
    elif abs(ev.duration_seconds - requested) > settings.duration_tolerance_seconds:
        out.append(failed("tech.duration", C.TECHNICAL, Severity.MAJOR,
                          f"clip is {ev.duration_seconds:.2f}s; {requested:.2f}s was requested",
                          subject=subject, data={"correctable": False},
                          fix_hint=f"The action must fill the full {requested:g} seconds"))
    else:
        out.append(passed("tech.duration", C.TECHNICAL,
                          f"{ev.duration_seconds:.2f}s (requested {requested:.2f}s)", subject=subject))
    if ev.width and ev.height:
        want = 16 / 9 if prompt["aspect_ratio"] == "16:9" else 9 / 16
        got = ev.width / ev.height
        if abs(got - want) > 0.02:
            out.append(failed("tech.aspect_ratio", C.TECHNICAL, Severity.MAJOR,
                              f"clip is {ev.width}x{ev.height}; {prompt['aspect_ratio']} was requested",
                              subject=subject, data={"correctable": False}))
        else:
            out.append(passed("tech.aspect_ratio", C.TECHNICAL,
                              f"{ev.width}x{ev.height} matches {prompt['aspect_ratio']}", subject=subject))
    if ev.media is None:
        out.append(not_run("tech.media_analysis", C.TECHNICAL, Severity.MINOR,
                           "the clip file is not obtainable from Vids; black/freeze analysis "
                           "runs on the export instead", subject=subject))
        return out
    analysis = await ffmpeg.analyse(ev.media.path, has_audio=bool(ev.has_audio))
    if not analysis.decode_ok:
        out.append(failed("tech.decode", C.TECHNICAL, Severity.MAJOR,
                          "the clip does not decode cleanly: " + "; ".join(analysis.decode_errors[:3]),
                          subject=subject, data={"correctable": False}))
    else:
        out.append(passed("tech.decode", C.TECHNICAL, "every frame decodes", subject=subject))
    duration = ev.duration_seconds or requested
    black = [(s, e) for s, e in analysis.black_intervals
             if not (s <= _EDGE_TOLERANCE and e <= _EDGE_TOLERANCE * 2)
             and not (s >= duration - _EDGE_TOLERANCE * 2)]
    if black:
        out.append(failed("tech.black_frames", C.TECHNICAL, Severity.MAJOR,
                          f"black picture from {black[0][0]:.2f}s to {black[0][1]:.2f}s",
                          subject=subject, data={"correctable": False, "intervals": black},
                          fix_hint="Keep the picture continuously lit and visible for the whole "
                                   "clip, with no cuts or fades to black"))
    else:
        out.append(passed("tech.black_frames", C.TECHNICAL, "no black stretches", subject=subject))
    if analysis.freeze_intervals:
        f0 = analysis.freeze_intervals[0]
        out.append(failed("tech.frozen", C.TECHNICAL, Severity.MAJOR,
                          f"the picture freezes from {f0[0]:.2f}s to {f0[1]:.2f}s",
                          subject=subject, data={"correctable": False},
                          fix_hint="Keep continuous, natural motion through the whole clip"))
    else:
        out.append(passed("tech.frozen", C.TECHNICAL, "motion throughout", subject=subject))
    return out


async def _speech(ai: AIService, registry: CapabilityRegistry, prompt: dict, ev: ClipEvidence,
                  subject: str, job_id: Any) -> list[RuleResult]:
    expected = list(prompt.get("spoken_lines") or [])
    if ev.audio is None:
        return [not_run("speech.transcript", C.CONTENT, Severity.MINOR,
                        "clip audio is not obtainable; speech is checked on the export",
                        subject=subject)]
    reply = await ai.generate(LLMRequest(
        purpose="transcribe", role=ModelRole.TRANSCRIBE, prompt=TRANSCRIBE_INSTRUCTION,
        media=[MediaPart(ev.audio.mime, ev.audio.path.read_bytes())], temperature=0.0,
        job_id=job_id, max_output_tokens=1024,
        simulation_hints={"transcript": ev.simulation_hints.get("transcript")},
    ))
    diff = unexpected_speech(reply.text, expected)
    out = []
    if diff["extra"]:
        out.append(failed("speech.unapproved", C.CONTENT, Severity.MAJOR,
                          "the clip contains speech nobody approved: "
                          + "; ".join(f'"{x}"' for x in diff["extra"][:3]),
                          subject=subject, data={"correctable": False, **diff},
                          fix_hint="Nobody speaks except the approved lines" if expected
                          else "Nobody speaks at all; natural ambient sound only"))
    if diff["missing"]:
        out.append(failed("speech.missing", C.CONTENT, Severity.MAJOR,
                          f"{len(diff['missing'])} approved line(s) are not heard",
                          subject=subject, data={"correctable": False, **diff},
                          fix_hint="Every approved line is spoken exactly as written"))
    if not out:
        out.append(passed("speech.transcript", C.CONTENT,
                          "speech matches the approved words" if expected else "no speech, as planned",
                          subject=subject, data=diff))
    return out


async def _visual(ai: AIService, scene: dict, prompt: dict, ev: ClipEvidence, brand_block: str,
                  continuity: list[tuple[str, Path]], subject: str, job_id: Any) -> list[RuleResult]:
    media = [MediaPart("image/jpeg" if f.path.suffix == ".jpg" else "image/png",
                       f.path.read_bytes(), f"{f.label or 'frame'} of this clip")
             for f in (ev.frames or ev.screenshots)[:6]]
    media += [MediaPart("image/jpeg" if p.suffix == ".jpg" else "image/png", p.read_bytes(),
                        f"reference: accepted scene {key}") for key, p in continuity[:3]]
    criteria = "\n".join(f"- {c}" for c in scene.get("validation_criteria", []))
    request = LLMRequest(
        purpose="scene_inspection", role=ModelRole.VISION, job_id=job_id, temperature=0.1,
        system="You are a broadcast QC reviewer for an insurance brand. Report only what the "
               "frames show; do not speculate.",
        prompt=(
            "Review frames of a generated scene against its brief. Report each problem as a "
            "finding: prompt_adherence, brand, visual_quality, continuity (compare with the "
            "reference frames of accepted scenes, if any), text (any visible text, captions, "
            "signage or logos are a problem), artifacts (distorted hands or faces, warping), "
            "narrative (does it serve the scene's purpose) or technical. Severity: MAJOR if "
            "the scene cannot be used as is, MINOR if it can but should be improved, INFO for "
            "observations. Say whether a text-instruction edit of this clip could fix it, and "
            "if so write that instruction positively. An empty findings list means the scene "
            "is good.\n\n"
            f"SCENE PURPOSE: {scene['narrative_purpose']}\n"
            f"CRITERIA:\n{criteria}\n\nPROMPT THE CLIP WAS MADE FROM:\n{prompt['prompt']}\n\n"
            f"{brand_block}"
        ),
        media=media, response_model=JudgeReport, max_output_tokens=2048,
        simulation_hints={"defect": ev.simulation_hints.get("defect")},
    )
    report: JudgeReport = (await ai.generate(request)).parsed
    out = []
    for f in report.findings:
        rule = f"judge.{f.aspect}"
        if f.severity is Severity.INFO:
            continue
        out.append(failed(rule, C.CONTENT if f.aspect != "brand" else C.BRAND_POLICY, f.severity,
                          f.message, judge="llm", subject=subject,
                          data={"correctable": bool(f.correctable_by_edit and f.edit_instruction),
                                "frame_timestamps": f.frame_timestamps},
                          fix_hint=f.edit_instruction if f.correctable_by_edit and f.edit_instruction
                          else _ASPECT_STEER.get(f.aspect, "Match the prompt exactly")))
    if not out:
        out.append(passed("judge.visual", C.CONTENT, report.summary or "no visual issues",
                          judge="llm", subject=subject))
    return out


def derive_verdict(results: list[RuleResult]) -> tuple[Verdict, list[str], str | None]:
    failures = [r for r in results if r.failed]
    reasons = [r.message for r in failures]
    if any(r.severity is Severity.BLOCKER for r in failures):
        return Verdict.HUMAN_REVIEW, reasons, None
    majors = [r for r in failures if r.severity is Severity.MAJOR]
    minors = [r for r in failures if r.severity is Severity.MINOR]
    # A MINOR "could not run here, checked later" is not a defect in the clip.
    minors = [r for r in minors if r.outcome.value != "NOT_RUN"]
    instructions = [r.fix_hint for r in [*majors, *minors]
                    if r.data.get("correctable") and r.fix_hint]
    instruction = "; ".join(dict.fromkeys(instructions)) or None
    if majors:
        if all(r.data.get("correctable") for r in majors) and instruction:
            return Verdict.NEEDS_EDIT, reasons, instruction
        return Verdict.NEEDS_REGENERATION, reasons, None
    if minors:
        if any(r.data.get("correctable") for r in minors) and instruction:
            return Verdict.NEEDS_EDIT, reasons, instruction
        return Verdict.PASS_WITH_WARNINGS, reasons, None
    return Verdict.PASS, reasons, None


# ======================================================================= correction policy
@dataclass(frozen=True)
class SceneBudget:
    takes_used: int
    edits_used: int
    max_takes: int
    max_edits: int
    generations_available: bool
    edit_verified: bool


@dataclass(frozen=True)
class Decision:
    action: CorrectionAction
    reason: str


def decide_correction(verdict: Verdict, b: SceneBudget) -> Decision:
    """Accept good scenes, edit what an edit can fix, regenerate only this scene when it
    cannot be edited, and hand persistent failures to a person."""
    if verdict in (Verdict.PASS, Verdict.PASS_WITH_WARNINGS):
        return Decision(CorrectionAction.ACCEPT_AND_LOCK, "the scene passed inspection")
    if verdict is Verdict.HUMAN_REVIEW:
        return Decision(CorrectionAction.HUMAN_REVIEW, "inspection could not decide safely")
    if verdict is Verdict.NEEDS_EDIT:
        if b.edit_verified and b.edits_used < b.max_edits and b.generations_available:
            return Decision(CorrectionAction.EDIT, "a local edit can fix the defect")
        verdict = Verdict.NEEDS_REGENERATION  # fall through: the edit path is not available
    if b.takes_used < b.max_takes and b.generations_available:
        return Decision(CorrectionAction.REGENERATE,
                        f"regenerating this scene only (take {b.takes_used + 1} of {b.max_takes})")
    if not b.generations_available:
        return Decision(CorrectionAction.HUMAN_REVIEW, "no generation budget remains")
    return Decision(CorrectionAction.HUMAN_REVIEW,
                    f"still failing after {b.takes_used} takes and {b.edits_used} edits")


def steering(results: list[RuleResult], limit: int = 3) -> list[str]:
    """Failed findings as positive instructions for the next take (ported pattern from
    media-generation `_what_went_wrong_last_time`): what to do, never a quote of what the
    rejected take did."""
    lines: list[str] = []
    for r in results:
        if r.failed and r.fix_hint and r.fix_hint not in lines:
            lines.append(r.fix_hint)
        if len(lines) >= limit:
            break
    return lines
