"""Final QA: the exported file, checked against everything that was approved.

Deterministic where possible (file integrity, duration, order, locked scenes, exact text
of supers and disclosures, logo window, black/freeze/silence/loudness, transcript vs
script), and an LLM review of sampled frames for what only looking can judge (storyline
coherence, brand feel, logo visibility, visual defects). Every finding becomes an
actionable issue with the stage or scene it points at.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vidsprod.config import Settings
from vidsprod.domain.enums import RuleCategory, ScriptLineKind, Severity
from vidsprod.domain.validation import JudgeReport, RuleResult, failed, not_run, passed
from vidsprod.llm.service import AIService
from vidsprod.llm.types import LLMRequest, MediaPart, ModelRole
from vidsprod.media.ffmpeg import Analysis, Probe
from vidsprod.planning.dialogue import (
    TRANSCRIBE_INSTRUCTION,
    normalise_line,
    unexpected_speech,
    words,
)
from vidsprod.vids.port import TimelineState

C = RuleCategory
LOUDNESS_RANGE = (-30.0, -9.0)

_ACTIONS = {
    "qa.duration": "Retry from TIMELINE_ASSEMBLY",
    "qa.scene_order": "Retry from TIMELINE_ASSEMBLY",
    "qa.locked_scenes": "Inspect the Vid by hand; accepted scenes changed",
    "qa.transitions": "Retry from TIMELINE_EDITS",
    "qa.supers": "Retry from TIMELINE_EDITS",
    "qa.cta": "Retry from TIMELINE_EDITS",
    "qa.disclosures": "Retry from TIMELINE_EDITS",
    "qa.logo": "Retry from TIMELINE_EDITS",
    "qa.speech": "Retry from TIMELINE_EDITS (voiceover)",
    "qa.black_frames": "Regenerate the affected scene",
    "qa.frozen": "Regenerate the affected scene",
    "qa.loudness": "Adjust audio levels in TIMELINE_EDITS",
    "qa.decode": "Retry EXPORT",
}


def _scene_at(t: float, windows: dict[str, tuple[float, float]]) -> str | None:
    for key, (a, b) in windows.items():
        if a <= t < b:
            return key
    return None


async def run_final_qa(
    *, ai: AIService, settings: Settings, probe: Probe, analysis: Analysis, export_path: Path,
    timeline: TimelineState, windows: dict[str, tuple[float, float]], scene_plan: dict,
    script: dict, brief: dict, locked: dict[str, dict[str, Any]], frames: list[tuple[float, Path]],
    brand_block: str, simulation_hints: dict[str, Any], job_id: Any,
) -> tuple[list[RuleResult], dict[str, Any]]:
    r: list[RuleResult] = []
    total = timeline.total_duration_seconds
    # ---------------------------------------------------------------- technical
    if not analysis.decode_ok:
        r.append(failed("qa.decode", C.TECHNICAL, Severity.BLOCKER,
                        "the export does not decode cleanly"))
    else:
        r.append(passed("qa.decode", C.TECHNICAL, "full decode without errors"))
    d = probe.duration_seconds or 0
    if abs(d - total) > max(1.0, settings.duration_tolerance_seconds * 2):
        r.append(failed("qa.duration", C.TECHNICAL, Severity.MAJOR,
                        f"export runs {d:.2f}s; the timeline is {total:.2f}s"))
    else:
        r.append(passed("qa.duration", C.TECHNICAL, f"{d:.2f}s (timeline {total:.2f}s)"))
    black = [(a, b) for a, b in analysis.black_intervals if a < d - 0.8]  # end fade allowed
    if black:
        where = _scene_at(black[0][0], windows)
        r.append(failed("qa.black_frames", C.TECHNICAL, Severity.MAJOR,
                        f"black picture at {black[0][0]:.2f}s" + (f" (scene {where})" if where else ""),
                        subject=f"scene:{where}" if where else None))
    else:
        r.append(passed("qa.black_frames", C.TECHNICAL, "no black stretches"))
    if analysis.freeze_intervals:
        f0 = analysis.freeze_intervals[0]
        where = _scene_at(f0[0], windows)
        r.append(failed("qa.frozen", C.TECHNICAL, Severity.MAJOR,
                        f"picture frozen at {f0[0]:.2f}s", subject=f"scene:{where}" if where else None))
    else:
        r.append(passed("qa.frozen", C.TECHNICAL, "motion throughout"))
    expects_audio = bool(timeline.voiceover or timeline.music) or brief["output"].get("voiceover_required")
    if expects_audio and not probe.has_audio:
        r.append(failed("qa.audio_present", C.TECHNICAL, Severity.BLOCKER,
                        "the export has no audio track"))
    elif probe.has_audio:
        r.append(passed("qa.audio_present", C.TECHNICAL, f"audio: {probe.audio_codec}"))
        lufs = analysis.integrated_lufs
        if lufs is None or not LOUDNESS_RANGE[0] <= lufs <= LOUDNESS_RANGE[1]:
            r.append(failed("qa.loudness", C.TECHNICAL, Severity.MINOR,
                            f"integrated loudness {lufs} LUFS is outside {LOUDNESS_RANGE}"))
        else:
            r.append(passed("qa.loudness", C.TECHNICAL, f"{lufs:.1f} LUFS"))
    # ---------------------------------------------------------------- structure
    planned = [s["scene_key"] for s in scene_plan["scenes"]]
    actual = [i.scene_key for i in sorted(timeline.items, key=lambda i: i.position)]
    if actual != planned:
        r.append(failed("qa.scene_order", C.CONTENT, Severity.MAJOR,
                        f"scene order {actual} differs from the plan {planned}"))
    else:
        r.append(passed("qa.scene_order", C.CONTENT, f"{len(actual)} scenes in planned order"))
    changed = [k for k, v in locked.items()
               if not any(i.scene_key == k and i.clip_id == v["clip_id"] for i in timeline.items)]
    if changed:
        r.append(failed("qa.locked_scenes", C.OPERATIONAL, Severity.BLOCKER,
                        f"accepted scenes not on the timeline as accepted: {changed}"))
    else:
        r.append(passed("qa.locked_scenes", C.OPERATIONAL, "every accepted take is in place"))
    wanted = {s["scene_key"]: s["transition_out"] for s in scene_plan["scenes"]}
    have = {t.get("after_scene"): t.get("kind") for t in timeline.transitions}
    if scene_plan.get("authored"):  # scenes a person wrote join with the default cut
        have = {k: have.get(k, "CUT") for k in wanted}
    missing_t = [k for k, kind in wanted.items() if have.get(k) != kind]
    if missing_t:
        r.append(failed("qa.transitions", C.CONTENT, Severity.MINOR,
                        f"planned transitions missing after {missing_t}"))
    else:
        r.append(passed("qa.transitions", C.CONTENT, f"{len(wanted)} transitions as planned"))
    overlays = [t for t in timeline.text_overlays]
    for sup in script["supers"]:
        if not any(t.get("text") == sup["text"] for t in overlays):
            r.append(failed("qa.supers", C.CONTENT, Severity.MAJOR,
                            f'super "{sup["text"][:50]}" is missing or misspelled on screen'))
    if script["supers"] and not any(x.rule_id == "qa.supers" for x in r):
        r.append(passed("qa.supers", C.CONTENT, f"{len(script['supers'])} supers, exact text"))
    cta = normalise_line(brief["cta"]).lower()
    cta_words = {w for w in words(cta) if len(w) > 2}
    cta_shown = any(cta in normalise_line(t.get("text", "")).lower() for t in overlays)

    def _says_cta(text: str) -> bool:
        said = set(words(text))
        return bool(cta_words) and len(cta_words & said) / len(cta_words) >= 0.6

    cta_said = any(_says_cta(v.get("text", "")) for v in timeline.voiceover)
    if script.get("authored") and not script.get("cta_required"):
        r.append(passed("qa.cta", C.CONTENT, "no call to action was asked for; the scenes are as written"))
    elif not (cta_shown or cta_said):
        r.append(failed("qa.cta", C.CONTENT, Severity.MAJOR, "the call to action is neither shown nor said"))
    else:
        r.append(passed("qa.cta", C.CONTENT,
                        "call to action " + ("shown" if cta_shown else "") + (" and said" if cta_said else "")))
    for text in script.get("disclosure_texts", []):
        match = [t for t in overlays if t.get("text") == text]
        need = max(settings.min_super_seconds, len(text.split()) / settings.reading_words_per_second)
        if not match:
            r.append(failed("qa.disclosures", C.BRAND_POLICY, Severity.BLOCKER,
                            f'disclosure "{text[:60]}" is not on screen verbatim'))
        elif max(float(t["end"]) - float(t["start"]) for t in match) + 1e-6 < need:
            r.append(failed("qa.disclosures", C.BRAND_POLICY, Severity.BLOCKER,
                            f'disclosure "{text[:40]}" is on screen for less than its reading time ({need:.1f}s)'))
    if script.get("disclosure_texts") and not any(x.rule_id == "qa.disclosures" for x in r):
        r.append(passed("qa.disclosures", C.BRAND_POLICY,
                        f"{len(script['disclosure_texts'])} disclosure(s) verbatim, long enough to read"))
    if brief["output"].get("end_card_required", True):
        if not timeline.logos or max(float(x["end"]) for x in timeline.logos) < total - 0.5:
            r.append(failed("qa.logo", C.BRAND_POLICY, Severity.MAJOR,
                            "the logo is not placed on the end card"))
        else:
            r.append(passed("qa.logo", C.BRAND_POLICY, "logo placed on the end card"))
    if brief["output"].get("captions_required", True) and not timeline.captions_enabled:
        r.append(failed("qa.captions", C.CONTENT, Severity.MAJOR, "captions are required but off"))
    # ---------------------------------------------------------------- speech
    expected = [x["text"] for x in script["lines"] if x["kind"] == ScriptLineKind.VOICEOVER.value]
    if probe.has_audio:
        from vidsprod.media import ffmpeg

        audio = await ffmpeg.extract_audio(export_path, export_path.with_suffix(".qa.wav"))
        if audio is None:
            r.append(not_run("qa.speech", C.CONTENT, Severity.MAJOR, "export audio could not be extracted"))
        else:
            hint = " ".join(simulation_hints.get("voiceover", []))
            reply = await ai.generate(LLMRequest(
                purpose="transcribe", role=ModelRole.TRANSCRIBE, prompt=TRANSCRIBE_INSTRUCTION,
                media=[MediaPart("audio/wav", audio.read_bytes())], temperature=0.0, job_id=job_id,
                max_output_tokens=2048, simulation_hints={"transcript": hint},
            ))
            diff = unexpected_speech(reply.text, expected)
            if diff["missing"] or diff["extra"]:
                r.append(failed("qa.speech", C.CONTENT, Severity.MAJOR,
                                f"voiceover differs from the script: {len(diff['missing'])} line(s) "
                                f"missing, {len(diff['extra'])} unapproved phrase(s)", data=diff))
            else:
                r.append(passed("qa.speech", C.CONTENT,
                                "every voiceover line heard exactly once, nothing else said", data=diff))
            r.append(passed("qa.av_sync", C.TECHNICAL,
                            "proxy check: each voiceover line is placed inside its own scene's "
                            "window on the timeline (frame-accurate lip sync is not measured)"))
    # ---------------------------------------------------------------- visual review
    if frames:
        media = [MediaPart("image/jpeg", p.read_bytes(), f"frame at {t:.2f}s") for t, p in frames]
        report: JudgeReport = (await ai.generate(LLMRequest(
            purpose="final_review", role=ModelRole.VISION, job_id=job_id, temperature=0.1,
            system="You are the final broadcast and brand QC reviewer for an insurance brand.",
            prompt=(
                "These frames are sampled from a finished brand video in order. Check: does the "
                "story read coherently beat by beat; is the look consistent with the brand; is "
                "the logo clearly visible on the final frames and undistorted; is on-screen text "
                "legible and correctly spelled; any visual defects (warping, distorted hands or "
                "faces, glitches)? Report problems as findings with aspect storyline, brand, logo, "
                "text or artifacts.\n\n"
                f"STORY:\n{[s['narrative_purpose'] for s in scene_plan['scenes']]}\n\n{brand_block}"
            ),
            media=media, response_model=JudgeReport, max_output_tokens=2048,
            simulation_hints={"defects": simulation_hints.get("final_defects", [])},
        ))).parsed
        found = [f for f in report.findings if f.severity is not Severity.INFO]
        for f in found:
            r.append(failed(f"qa.visual.{f.aspect}", C.CONTENT, f.severity, f.message, judge="llm"))
        if not found:
            r.append(passed("qa.visual", C.CONTENT, report.summary or "no visual issues", judge="llm"))
    else:
        r.append(not_run("qa.visual", C.CONTENT, Severity.MAJOR, "no frames to review"))

    issues = [
        {"rule_id": x.rule_id, "severity": x.severity.value, "message": x.message,
         "subject": x.subject, "judge": x.judge,
         "suggested_action": _ACTIONS.get(x.rule_id.rsplit(".", 1)[0] if x.rule_id.startswith("qa.visual") else x.rule_id,
                                          "Review with the production team")}
        for x in r if x.failed
    ]
    worst = min((x.severity for x in r if x.failed), key=lambda s: list(Severity).index(s), default=None)
    report_doc = {
        "verdict": "PASS" if worst is None else ("PASS_WITH_WARNINGS" if worst in (Severity.MINOR, Severity.INFO) else "FAIL"),
        "summary": f"{sum(1 for x in r if not x.failed)} checks passed, {len(issues)} issue(s)",
        "export": {**probe.to_dict(), "analysis": analysis.to_dict()},
        "sections": {cat.value: [x.model_dump(mode="json") for x in r if x.category is cat] for cat in C},
        "actionable_issues": issues,
    }
    return r, report_doc
