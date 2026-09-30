"""The planner decides duration and scene count from the script, not from constants."""

from __future__ import annotations

import pytest

from vidsprod.planning.dialogue import SpeechRate
from vidsprod.planning.durations import ClipLimits, build_skeletons

VIDS = ClipLimits(min_seconds=3, max_seconds=10)  # documented Vids range; see capabilities


def _beats(n):
    return [{"id": f"b{i}"} for i in range(1, n + 1)]


def _line(i, beat, n_words):
    return {"id": f"l{i}", "beat_id": beat, "text": " ".join(["word"] * n_words)}


def _plan(beats, lines, supers=(), min_total=6, max_total=90, **kw):
    return build_skeletons(beats=beats, lines=lines, supers=list(supers), limits=VIDS,
                           min_total=min_total, max_total=max_total, **kw)


def test_scene_count_follows_the_story_not_a_constant():
    short = _plan(_beats(2), [_line(1, "b1", 8), _line(2, "b2", 8)])
    long = _plan(_beats(5), [_line(i, f"b{i}", 12) for i in range(1, 6)])
    assert len(short.scenes) == 2
    assert len(long.scenes) == 5
    assert short.total_seconds < long.total_seconds
    assert not short.problems and not long.problems


def test_total_duration_is_derived_from_speech():
    few = _plan(_beats(3), [_line(i, f"b{i}", 5) for i in range(1, 4)])
    many = _plan(_beats(3), [_line(i, f"b{i}", 18) for i in range(1, 4)])
    assert many.total_seconds > few.total_seconds


def test_every_scene_is_within_the_clip_limits():
    plan = _plan(_beats(4), [_line(i, f"b{i}", n) for i, n in enumerate([3, 9, 17, 19], 1)])
    for scene in plan.scenes:
        assert VIDS.min_seconds <= scene.duration_seconds <= VIDS.max_seconds


def test_a_beat_with_too_much_speech_is_split_across_scenes_in_order():
    lines = [_line(1, "b1", 12), _line(2, "b1", 12), _line(3, "b1", 12)]
    plan = _plan(_beats(1), lines)
    assert len(plan.scenes) == 3
    assert [s.line_ids for s in plan.scenes] == [["l1"], ["l2"], ["l3"]]
    assert all(s.beat_id == "b1" for s in plan.scenes)


def test_every_line_is_allocated_exactly_once():
    lines = [_line(i, f"b{(i % 3) + 1}", 6 + i) for i in range(1, 8)]
    plan = _plan(_beats(3), lines)
    allocated = [lid for s in plan.scenes for lid in s.line_ids]
    assert sorted(allocated) == sorted(x["id"] for x in lines)


def test_a_line_longer_than_any_clip_is_a_blocking_problem():
    plan = _plan(_beats(1), [_line(1, "b1", 40)])
    assert any("longer than the longest clip" in p for p in plan.problems)


def test_a_silent_beat_becomes_a_visual_scene():
    plan = _plan(_beats(2), [_line(1, "b2", 8)])
    assert plan.scenes[0].line_ids == [] and plan.scenes[0].reason == "visual beat"


def test_supers_need_reading_time():
    supers = [{"id": "s1", "beat_id": "b1", "text": " ".join(["word"] * 24)}]
    plan = _plan(_beats(1), [], supers=supers, reading_wps=3.0)
    assert plan.scenes[0].duration_seconds >= 8


def test_a_short_script_is_stretched_to_the_brief_minimum():
    plan = _plan(_beats(2), [_line(1, "b1", 4), _line(2, "b2", 4)], min_total=15)
    assert plan.total_seconds >= 15
    assert not plan.problems


def test_a_script_too_long_for_the_maximum_is_refused_with_the_arithmetic():
    plan = _plan(_beats(4), [_line(i, f"b{i}", 18) for i in range(1, 5)], max_total=20)
    assert any("maximum is 20s" in p and "cut about" in p for p in plan.problems)


def test_an_exact_total_is_honoured_with_the_ported_balancer():
    plan = _plan(_beats(3), [_line(i, f"b{i}", 6) for i in range(1, 4)], exact_total=21)
    assert plan.total_seconds == pytest.approx(21)


def test_different_speech_rates_change_the_plan():
    lines = [_line(i, f"b{i}", 14) for i in range(1, 4)]
    slow = _plan(_beats(3), lines, rate=SpeechRate(words_per_second=2.0))
    fast = _plan(_beats(3), lines, rate=SpeechRate(words_per_second=3.2))
    assert slow.total_seconds > fast.total_seconds


def test_clip_limits_come_from_the_caller():
    tight = build_skeletons(beats=_beats(1), lines=[_line(1, "b1", 12), _line(2, "b1", 12)],
                            supers=[], limits=ClipLimits(3, 6), min_total=1, max_total=90)
    loose = build_skeletons(beats=_beats(1), lines=[_line(1, "b1", 12), _line(2, "b1", 12)],
                            supers=[], limits=ClipLimits(3, 20), min_total=1, max_total=90)
    assert len(tight.scenes) > len(loose.scenes)
