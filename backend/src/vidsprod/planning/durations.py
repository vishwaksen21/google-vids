"""Dynamic duration and scene planning.

Nothing here is a fixed number. The total duration and the scene count fall out of the
script (how much there is to say and read, beat by beat), the brief's duration bounds, and
the clip lengths the capability registry says Vids can generate.

`plan_scene_durations` and `match_durations_to_dialogue` are ported from
phi-service-layer/media-generation/backend/app/application/scene_service.py (2026-09-29);
`build_skeletons` is new and is what decides how many scenes a film needs.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from vidsprod.planning.dialogue import (
    DEFAULT_RATE,
    SpeechRate,
    count_words,
    min_seconds_for,
    speech_seconds,
)


# ------------------------------------------------------------------ ported
def plan_scene_durations(
    target_duration: float, scene_count: int, allowed: Sequence[float]
) -> list[float]:
    """A per-scene length plan from the allowed set that adds up to the target.

    Starts from the allowed length nearest an even division and moves single scenes up or
    down one step until the total lands — 22s over four scenes of {4,6,8} is 6+6+6+4.
    """
    options = sorted({float(v) for v in (allowed or ()) if v > 0})
    if not options or scene_count <= 0 or target_duration <= 0:
        return []
    even = target_duration / scene_count
    plan = [min(options, key=lambda v: (abs(v - even), -v))] * scene_count
    plan = list(plan)

    for _ in range(scene_count * len(options) * 2):
        gap = target_duration - sum(plan)
        if abs(gap) < 0.01:
            break
        best = None
        for index, current in enumerate(plan):
            for option in options:
                if option == current:
                    continue
                after = abs(target_duration - (sum(plan) - current + option))
                if best is None or after < best[0]:
                    best = (after, index, option)
        if best is None or best[0] >= abs(gap):
            break
        plan[best[1]] = best[2]
    return sorted(plan, reverse=True)


def match_durations_to_dialogue(scenes: list[dict], rate: SpeechRate = DEFAULT_RATE) -> list[str]:
    """Give the longer speech the longer scenes, without moving a single word.

    A permutation of the chosen lengths, so the total is unchanged. Returns the names of
    scenes whose speech still cannot fit — a planning failure a reviewer must see.
    """
    durations = sorted((float(s["duration_seconds"]) for s in scenes), reverse=True)
    needs = [
        (speech_seconds(s.get("dialogue_lines") or [], rate), i) for i, s in enumerate(scenes)
    ]
    if all(need <= float(scenes[i]["duration_seconds"]) * rate.dialogue_share for need, i in needs):
        return []
    order = [i for _, i in sorted(needs, key=lambda item: (-item[0], item[1]))]
    for slot, index in enumerate(order):
        scenes[index]["duration_seconds"] = durations[slot]
    return [
        scenes[i].get("title") or f"scene {i + 1}"
        for need, i in needs
        if need > float(scenes[i]["duration_seconds"]) * rate.dialogue_share
    ]


# ------------------------------------------------------------------ new: skeletons
@dataclass(frozen=True)
class ClipLimits:
    """What Vids can generate, from the capability registry (never hardcoded here)."""

    min_seconds: float
    max_seconds: float
    step_seconds: float = 1.0

    def allowed(self) -> list[float]:
        values, value = [], self.min_seconds
        while value <= self.max_seconds + 1e-9:
            values.append(round(value, 3))
            value += self.step_seconds
        return values

    def snap_up(self, seconds: float) -> float:
        for value in self.allowed():
            if value + 1e-9 >= seconds:
                return value
        return self.max_seconds


@dataclass
class Skeleton:
    scene_key: str
    order: int
    beat_id: str
    line_ids: list[str]
    super_ids: list[str]
    duration_seconds: float
    need_seconds: float
    reason: str


@dataclass
class SkeletonPlan:
    scenes: list[Skeleton]
    total_seconds: float
    problems: list[str] = field(default_factory=list)  # blocking planning failures
    notes: list[str] = field(default_factory=list)


def reading_seconds(text: str, words_per_second: float, minimum: float) -> float:
    return max(minimum, count_words(text) / words_per_second)


def build_skeletons(
    *,
    beats: Sequence[dict],
    lines: Sequence[dict],
    supers: Sequence[dict],
    limits: ClipLimits,
    min_total: float,
    max_total: float,
    rate: SpeechRate = DEFAULT_RATE,
    reading_wps: float = 3.0,
    min_super_seconds: float = 2.0,
    visual_beat_seconds: float = 4.0,
    pacing_seconds: float = 0.5,
    exact_total: float | None = None,
) -> SkeletonPlan:
    """Decide how many scenes the film needs and how long each is.

    Per beat, in story order: the time its speech needs (at the configured speech rate,
    within the share of a scene speech may occupy), the time its supers need to be read,
    or a visual beat if it has neither. A beat that needs more than the longest clip is
    split across consecutive scenes, keeping its lines in order. Then the total is
    reconciled with the brief's bounds: stretched (within clip limits) if short, refused
    with the arithmetic if the script cannot fit the maximum.
    """
    problems: list[str] = []
    notes: list[str] = []
    lines_by_beat: dict[str, list[dict]] = {}
    for line in lines:
        lines_by_beat.setdefault(line["beat_id"], []).append(line)
    supers_by_beat: dict[str, list[dict]] = {}
    for sup in supers:
        supers_by_beat.setdefault(sup["beat_id"], []).append(sup)

    known_beats = {b["id"] for b in beats}
    for line in lines:
        if line["beat_id"] not in known_beats:
            problems.append(f"line {line['id']} belongs to unknown beat {line['beat_id']}")

    skeletons: list[Skeleton] = []

    def add(beat_id: str, chunk: list[dict], sups: list[dict], reason: str) -> None:
        speech_need = min_seconds_for(chunk, rate) if chunk else 0.0
        super_need = sum(
            reading_seconds(s["text"], reading_wps, min_super_seconds) for s in sups
        )
        need = max(speech_need, super_need)
        base = need + pacing_seconds if need else visual_beat_seconds
        duration = limits.snap_up(max(base, limits.min_seconds))
        skeletons.append(
            Skeleton(
                scene_key=f"S{len(skeletons) + 1:02d}", order=len(skeletons) + 1,
                beat_id=beat_id, line_ids=[x["id"] for x in chunk],
                super_ids=[s["id"] for s in sups], duration_seconds=duration,
                need_seconds=round(need, 2), reason=reason,
            )
        )

    capacity = limits.max_seconds * rate.dialogue_share * rate.words_per_second  # words/scene
    for beat in beats:
        beat_lines = lines_by_beat.get(beat["id"], [])
        beat_supers = supers_by_beat.get(beat["id"], [])
        for line in beat_lines:
            if min_seconds_for([line], rate) > limits.max_seconds + 1e-9:
                problems.append(
                    f"line {line['id']} ({count_words(line['text'])} words) needs "
                    f"{min_seconds_for([line], rate):.1f}s, longer than the longest clip "
                    f"({limits.max_seconds:.0f}s); it must be shortened to about "
                    f"{int(capacity)} words or split into two lines"
                )
        # Greedy split of the beat's lines into scenes that each fit one clip.
        chunks: list[list[dict]] = []
        current: list[dict] = []
        for line in beat_lines:
            trial = [*current, line]
            if current and min_seconds_for(trial, rate) > limits.max_seconds:
                chunks.append(current)
                current = [line]
            else:
                current = trial
        if current:
            chunks.append(current)
        if not chunks:
            add(beat["id"], [], beat_supers, "visual beat" if not beat_supers else "supers")
            continue
        for index, chunk in enumerate(chunks):
            # Supers go with the last scene of the beat (where the beat lands).
            sups = beat_supers if index == len(chunks) - 1 else []
            reason = "speech" if len(chunks) == 1 else f"speech, part {index + 1}/{len(chunks)}"
            add(beat["id"], chunk, sups, reason)

    total = sum(s.duration_seconds for s in skeletons)
    low = max(min_total, limits.min_seconds)
    high = max_total

    if exact_total is not None:
        planned = plan_scene_durations(exact_total, len(skeletons), limits.allowed())
        if not planned or abs(sum(planned) - exact_total) > limits.step_seconds:
            problems.append(
                f"{exact_total:.0f}s cannot be built from {len(skeletons)} scenes of "
                f"{limits.min_seconds:.0f}-{limits.max_seconds:.0f}s"
            )
        else:
            as_dicts = [
                {"title": s.scene_key, "duration_seconds": planned[i],
                 "dialogue_lines": [x for x in lines if x["id"] in s.line_ids]}
                for i, s in enumerate(skeletons)
            ]
            unfit = match_durations_to_dialogue(as_dicts, rate)
            problems.extend(f"{name}: speech does not fit its scene" for name in unfit)
            for s, d in zip(skeletons, as_dicts, strict=True):
                s.duration_seconds = float(d["duration_seconds"])
            total = sum(s.duration_seconds for s in skeletons)
            notes.append(f"brief fixes the total at {exact_total:.0f}s")

    if total < low - 1e-9:
        # Stretch: give time to the scenes with the least slack first, never past a clip.
        shortfall = low - total
        for s in sorted(skeletons, key=lambda s: s.duration_seconds):
            if shortfall <= 1e-9:
                break
            room = limits.max_seconds - s.duration_seconds
            grant = min(room, math.ceil(shortfall / limits.step_seconds) * limits.step_seconds)
            s.duration_seconds += grant
            shortfall -= grant
        total = sum(s.duration_seconds for s in skeletons)
        notes.append(f"stretched to the {low:.0f}s minimum")
        if total < low - 1e-9:
            problems.append(
                f"the script supports at most {total:.0f}s across {len(skeletons)} scenes "
                f"but the brief requires at least {low:.0f}s; add a beat or more script"
            )

    if total > high + 1e-9:
        over = total - high
        words_over = int(over * rate.dialogue_share * rate.words_per_second) + 1
        problems.append(
            f"the script needs {total:.0f}s but the maximum is {high:.0f}s; cut about "
            f"{words_over} words or one beat"
        )
    return SkeletonPlan(scenes=skeletons, total_seconds=total, problems=problems, notes=notes)
