"""Timeline planning and edit verification.

The edit plan is built deterministically from the approved plan, script and brief, using
the real durations of the locked takes on the timeline: transitions (edit grammar from the
scene plan), voiceover lines placed inside their scenes, supers with reading time, the
disclosures for at least their reading time, the logo on the end card, captions.

Every operation carries the check that proves it persisted, and after every operation the
locked scenes are re-read and compared with what was accepted.
"""

from __future__ import annotations

from typing import Any

from vidsprod.config import Settings
from vidsprod.domain.enums import RuleCategory, ScriptLineKind, Severity, TimelineOpKind
from vidsprod.domain.validation import RuleResult, failed, passed
from vidsprod.planning.dialogue import count_words, normalise_line
from vidsprod.vids.port import TimelineState

C = RuleCategory

CAPABILITY_FOR = {
    TimelineOpKind.ARRANGE: "timeline.arrange",
    TimelineOpKind.TRANSITION: "timeline.transition",
    TimelineOpKind.TEXT_OVERLAY: "timeline.text_overlay",
    TimelineOpKind.LOGO: "timeline.logo",
    TimelineOpKind.VOICEOVER: "timeline.voiceover",
    TimelineOpKind.MUSIC: "timeline.music",
    TimelineOpKind.CAPTIONS: "timeline.captions",
}


# The uploaded-logo recipe is an image placed top right (verified image upload + layout),
# not the unverified end-card logo that CAPABILITY_FOR maps LOGO to.
LOGO_CAPABILITY = "timeline.image"


def scene_windows(state: TimelineState) -> dict[str, tuple[float, float]]:
    windows, t = {}, 0.0
    for item in sorted(state.items, key=lambda i: i.position):
        windows[item.scene_key] = (round(t, 3), round(t + item.duration_seconds, 3))
        t += item.duration_seconds
    return windows


def reading_time(text: str, settings: Settings) -> float:
    return max(settings.min_super_seconds, count_words(text) / settings.reading_words_per_second)


def plan_edits(*, scene_plan: dict, script: dict, brief: dict, profile: dict,
               state: TimelineState, settings: Settings, logo_path: str | None = None) -> list[dict[str, Any]]:
    windows = scene_windows(state)
    total = max((end for _, end in windows.values()), default=0.0)
    scenes = scene_plan["scenes"]
    lines = {x["id"]: x for x in script["lines"]}
    supers = {s["id"]: s for s in script["supers"]}
    ops: list[dict[str, Any]] = []

    def add(kind: TimelineOpKind, params: dict, affects: list[str], required: bool,
            severity: Severity = Severity.MAJOR, capability: str | None = None) -> None:
        ops.append({"kind": kind.value, "params": params, "affects_scenes": affects,
                    "required": required, "severity": severity.value,
                    **({"capability": capability} if capability else {})})

    # Scenes written by a person (planning/authored.py): a scene's text is on screen for the
    # whole scene, as a Vids text box is, and scenes join with the default cut.
    authored = bool(scene_plan.get("authored"))
    for index, scene in enumerate(scenes):
        key = scene["scene_key"]
        start, end = windows.get(key, (0.0, 0.0))
        duration = 0.4 if scene["transition_out"] == "DISSOLVE" else (
            0.5 if scene["transition_out"] == "FADE" else 0.0)
        if not (authored and scene["transition_out"] == "CUT"):  # a cut needs nothing applied
            add(TimelineOpKind.TRANSITION, {"after_scene": key, "kind": scene["transition_out"],
                                            "duration": duration},
                [key] + ([scenes[index + 1]["scene_key"]] if index + 1 < len(scenes) else []),
                required=False, severity=Severity.MINOR)
        cursor = start + 0.3
        for line_id in scene["line_ids"]:
            line = lines[line_id]
            if line["kind"] == ScriptLineKind.VOICEOVER.value:
                add(TimelineOpKind.VOICEOVER, {"line_id": line_id, "text": line["text"],
                                               "start": round(cursor, 2), "scene_key": key},
                    [key], required=brief["output"].get("voiceover_required", True))
                cursor += count_words(line["text"]) / settings.speech_words_per_second + 0.2
        for super_id in scene["super_ids"]:
            text = supers[super_id]["text"]
            s0 = start if authored else start + 0.5
            e0 = end if authored else min(end, s0 + reading_time(text, settings))
            add(TimelineOpKind.TEXT_OVERLAY,
                {"text": text, "start": round(s0, 2), "end": round(e0, 2),
                 "position": "lower", "role": "super", "super_id": super_id, "scene_key": key},
                [key], required=True)
    # Disclosures sit at the top of the frame at the end of the film, each for at least its
    # reading time; if the last scene is too short they start earlier.
    for index, text in enumerate(script.get("disclosure_texts", [])):
        need = reading_time(text, settings)
        s0 = max(0.0, total - need - 0.2)
        extra: dict[str, Any] = {}
        if authored and scenes:  # a text box on the last scene, for that whole scene
            last = scenes[-1]["scene_key"]
            s0 = windows.get(last, (s0, total))[0]
            extra = {"scene_key": last}
        add(TimelineOpKind.TEXT_OVERLAY,
            {"text": text, "start": round(s0, 2), "end": round(total, 2), "position": "upper",
             "role": "disclosure", "disclosure_index": index, **extra},
            [k for k, (a, b) in windows.items() if b > s0], required=True, severity=Severity.BLOCKER)
    if logo_path:  # the uploaded logo, top right, for each whole scene
        for scene in scenes:
            key = scene["scene_key"]
            a, b = windows.get(key, (0.0, 0.0))
            add(TimelineOpKind.LOGO, {"asset_path": logo_path, "position": "top-right",
                                      "start": round(a, 2), "end": round(b, 2), "scene_key": key},
                [key], required=True, capability=LOGO_CAPABILITY)
    if brief["output"].get("end_card_required", True) and scenes:
        last = scenes[-1]["scene_key"]
        a, b = windows.get(last, (0.0, total))
        add(TimelineOpKind.LOGO, {"asset": "brand_logo", "start": round(max(a, b - 3.0), 2),
                                  "end": round(b, 2), "position": profile.get("logo_position", "bottom-right")},
            [last], required=True)
    if brief["output"].get("captions_required", True):
        add(TimelineOpKind.CAPTIONS, {"enabled": True}, [], required=True)
    for seq, op in enumerate(ops, 1):
        op["seq"] = seq
        op["op_key"] = f"op{seq:03d}-{op['kind'].lower()}"
    return ops


def verify_op(op: dict[str, Any], state: TimelineState) -> tuple[bool, str]:
    """Did this operation persist? Read back from the timeline, never assumed."""
    kind, p = TimelineOpKind(op["kind"]), op["params"]
    if kind is TimelineOpKind.TRANSITION:
        ok = any(t.get("after_scene") == p["after_scene"] and t.get("kind") == p["kind"]
                 for t in state.transitions)
        return ok, f"transition {p['kind']} after {p['after_scene']}"
    if kind is TimelineOpKind.VOICEOVER:
        ok = any(normalise_line(v.get("text", "")) == normalise_line(p["text"]) for v in state.voiceover)
        return ok, f'voiceover "{p["text"][:40]}"'
    if kind is TimelineOpKind.TEXT_OVERLAY:
        ok = any(t.get("text") == p["text"] and abs(float(t.get("start", -1)) - p["start"]) < 0.11
                 and abs(float(t.get("end", -1)) - p["end"]) < 0.11 for t in state.text_overlays)
        return ok, f'text "{p["text"][:40]}" {p["start"]}-{p["end"]}s'
    if kind is TimelineOpKind.LOGO:
        ok = any(abs(float(x.get("start", -1)) - p["start"]) < 0.11
                 and abs(float(x.get("end", -1)) - p["end"]) < 0.11 for x in state.logos)
        return ok, f"logo {p['start']}-{p['end']}s"
    if kind is TimelineOpKind.CAPTIONS:
        return state.captions_enabled == bool(p["enabled"]), "captions enabled"
    if kind is TimelineOpKind.MUSIC:
        return bool(state.music), "music track"
    if kind is TimelineOpKind.ARRANGE:
        order = [i.scene_key for i in sorted(state.items, key=lambda i: i.position)]
        return order == p["order"], "scene order " + ",".join(order)
    return False, "unknown operation"


def verify_locked(state: TimelineState, locked: dict[str, dict[str, Any]],
                  subject: str) -> RuleResult:
    """The accepted scenes are exactly what was accepted: same clip, same fingerprint."""
    on_timeline = {i.scene_key: i for i in state.items}
    changed = []
    for key, expect in locked.items():
        item = on_timeline.get(key)
        if item is None:
            changed.append(f"{key} is missing")
        elif item.clip_id != expect["clip_id"]:
            changed.append(f"{key} now holds a different clip")
        elif expect.get("fingerprint") and item.fingerprint and item.fingerprint != expect["fingerprint"]:
            changed.append(f"{key} content changed")
    if changed:
        return failed("timeline.locked_scenes", C.OPERATIONAL, Severity.BLOCKER,
                      "accepted scenes changed: " + "; ".join(changed), subject=subject)
    return passed("timeline.locked_scenes", C.OPERATIONAL,
                  f"all {len(locked)} accepted scenes unchanged", subject=subject)
