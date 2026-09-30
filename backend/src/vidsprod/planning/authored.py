"""Planning documents built from scenes the person wrote (`BriefInput.scenes`).

No AI is involved: each scene's prompt is sent to Google Vids exactly as written, its words
are shown on screen exactly as written, and its length is the clip's length. The documents
have the same shape as the AI planners' so production, the timeline edits and final QA read
them unchanged; they carry `"authored": True` where downstream behaviour differs:

* a scene's text is on screen for the whole scene (a text box on a Vids scene is), so its
  overlay window is the scene window;
* scenes are joined by plain cuts (the Vids default), so no transition is applied;
* a call to action is checked only when the person gave one.
"""

from __future__ import annotations

from typing import Any

from vidsprod.agents.common import check_forbidden
from vidsprod.config import Settings
from vidsprod.domain.enums import RuleCategory, Severity
from vidsprod.domain.validation import RuleResult, failed, passed
from vidsprod.planning.durations import ClipLimits

C = RuleCategory


def is_authored(brief_input: dict[str, Any]) -> bool:
    return bool(brief_input.get("scenes"))


def shown_text(text: str) -> str:
    """Vids capitalises the first letter of a text box (observed), so the plan says so too."""
    text = " ".join(text.split())
    return text[:1].upper() + text[1:]


def _key(i: int) -> str:
    return f"S{i + 1:02d}"


def creative_plan(brief_input: dict[str, Any]) -> dict[str, Any]:
    scenes = brief_input["scenes"]
    beats = [{"id": f"b{i + 1}", "name": f"Scene {i + 1}",
              "purpose": shown_text(s["on_screen_text"]) if s.get("on_screen_text") else f"Scene {i + 1}",
              "emotion": "as written"} for i, s in enumerate(scenes)]
    return {
        "authored": True,
        "title": brief_input["raw_brief"][:80],
        "logline": brief_input["raw_brief"][:300],
        "concept": "Written scene by scene by the person; used word for word.",
        "visual_direction": "As described in each scene's prompt.",
        "emotional_direction": "As written.",
        "beats": beats,
        "characters": [],
        "locations": [{"id": "loc1", "name": "As described", "description": "Each scene describes its own setting."}],
        "objective_beat_id": beats[0]["id"],
        "cta_beat_id": beats[-1]["id"],
    }


def text_on_video(brief_input: dict[str, Any]) -> bool:
    return (brief_input.get("output") or {}).get("on_screen_text", True)


def script(brief_input: dict[str, Any], campaign_brief: dict[str, Any]) -> dict[str, Any]:
    show = text_on_video(brief_input)  # False: no text at all on the video, disclosures included
    supers = [{"id": f"sup{i + 1}", "beat_id": f"b{i + 1}", "text": shown_text(s["on_screen_text"]),
               "purpose": "on-screen text"}
              for i, s in enumerate(brief_input["scenes"]) if show and s.get("on_screen_text")]
    return {
        "authored": True,
        "lines": [],  # nothing is spoken by a voiceover; speech, if any, is in a scene's own prompt
        "supers": supers,
        "cta_line_id": "",
        "cta_required": bool(brief_input.get("cta")),
        "disclosure_texts": [d["text"] for d in campaign_brief.get("disclosures", [])] if show else [],
        "estimated_speech_seconds": 0.0,
    }


def scene_plan(brief_input: dict[str, Any]) -> dict[str, Any]:
    scenes = []
    show = text_on_video(brief_input)
    for i, s in enumerate(brief_input["scenes"]):
        scenes.append({
            "scene_key": _key(i), "order": i + 1, "beat_id": f"b{i + 1}",
            "narrative_purpose": shown_text(s["on_screen_text"]) if s.get("on_screen_text") else f"Scene {i + 1}",
            "duration_seconds": float(s["duration_seconds"]),
            "visual_description": s["prompt"], "camera_direction": "", "lighting": "",
            "character_ids": [], "location_id": "loc1", "line_ids": [],
            "super_ids": [f"sup{i + 1}"] if show and s.get("on_screen_text") else [],
            "transition_out": "CUT", "depends_on": [],
            "validation_criteria": ["The clip shows what the scene's prompt describes."],
        })
    return {"authored": True, "scenes": scenes, "rationale": "Scenes written by the person.",
            "total_duration_seconds": float(sum(x["duration_seconds"] for x in scenes))}


def prompts(brief_input: dict[str, Any]) -> dict[str, Any]:
    aspect = (brief_input.get("output") or {}).get("aspect_ratio", "16:9")
    return {"authored": True, "prompts": [
        {"scene_key": _key(i), "prompt": s["prompt"], "duration_seconds": float(s["duration_seconds"]),
         "aspect_ratio": aspect, "continuity_tokens": [], "spoken_lines": [],
         "reference_asset_ids": [s["photo_asset_id"]] if s.get("photo_asset_id") else []}
        for i, s in enumerate(brief_input["scenes"])]}


def validate(brief_input: dict[str, Any], *, limits: ClipLimits, settings: Settings,
             forbidden: list[str], aspect_ratios: list[str],
             disclosures: list[str] | None = None) -> list[RuleResult]:
    """The checks that still apply when a person wrote the scenes. They run before anything
    is generated, so a plan that could not pass final QA stops here, not after spending."""
    r: list[RuleResult] = []
    scenes = brief_input["scenes"]
    out = brief_input.get("output") or {}
    last = scenes[-1]["duration_seconds"] if scenes else 0
    for text in (disclosures or []) if text_on_video(brief_input) else []:  # shown on the last scene, for that whole scene
        need = max(settings.min_super_seconds, len(text.split()) / settings.reading_words_per_second)
        if last + 1e-6 < need:
            r.append(failed("plan.disclosure_time", C.BRAND_POLICY, Severity.BLOCKER,
                            f'the disclosure "{text[:60]}" needs {need:.1f}s on screen; the last scene is {last}s',
                            fix_hint=(f"Make the last scene at least {min(limits.max_seconds, need):.0f}s long"
                                      if need <= limits.max_seconds else
                                      "Shorten the disclosure: no single scene is long enough to read it")))
    for i, s in enumerate(scenes):
        key = _key(i)
        if not limits.min_seconds <= s["duration_seconds"] <= limits.max_seconds:
            r.append(failed("plan.clip_limits", C.TECHNICAL, Severity.BLOCKER,
                            f"{key} is {s['duration_seconds']}s; a clip is {limits.min_seconds:g}–{limits.max_seconds:g}s",
                            subject=f"scene:{key}"))
        if s.get("photo_asset_id") and "@" in s["prompt"]:
            r.append(failed("prompts.no_mention", C.TECHNICAL, Severity.BLOCKER,
                            f"{key}: with a photo the prompt is typed after the photo's chip; remove the '@'",
                            subject=f"scene:{key}",
                            fix_hint="Describe the person as 'the person in the photo', without '@'"))
    if out.get("aspect_ratio", "16:9") not in aspect_ratios:
        r.append(failed("prompts.aspect_ratio", C.TECHNICAL, Severity.BLOCKER,
                        f"{out.get('aspect_ratio')} is not a supported aspect ratio ({aspect_ratios})"))
    total = sum(s["duration_seconds"] for s in scenes)
    low, high = out.get("min_duration_seconds"), out.get("max_duration_seconds")
    if (low and total < low) or (high and total > high):
        r.append(failed("plan.total_duration", C.CONTENT, Severity.MAJOR,
                        f"the scenes add up to {total}s, outside the {low or 0:g}–{high or '∞'}s you asked for"))
    r.append(check_forbidden(rule_id="plan.forbidden_terms", subject="on-screen text and prompts",
                             texts=[s.get("on_screen_text") or "" for s in scenes] + [s["prompt"] for s in scenes],
                             forbidden=forbidden))
    if not any(x.failed and x.severity in (Severity.BLOCKER, Severity.MAJOR) for x in r):
        r.append(passed("plan.authored", C.CONTENT,
                        f"{len(scenes)} scene{'s' if len(scenes) != 1 else ''} written by you, {total}s, used word for word"))
    return r
