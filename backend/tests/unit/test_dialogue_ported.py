"""Ported from phi-service-layer/media-generation/backend/tests/test_scene_spec.py and
test_dialogue_craft.py. The original tests went through that project's `validate()` and
Veo prompt rendering; here they call the ported pure checks directly, with the same
inputs and the same assertions."""

from __future__ import annotations

import pytest

from vidsprod.planning.dialogue import (
    SpeechRate,
    check_allocation,
    check_dialogue,
    check_dialogue_fits,
    check_vocalisations,
    count_words,
    dialogue_language,
    is_disembodied,
    missing_from_scenes,
    normalise_line,
    speakable_words,
    speakable_words_floor,
    speech_seconds,
    speech_words,
    unexpected_speech,
    words,
)
from vidsprod.planning.durations import match_durations_to_dialogue, plan_scene_durations

FATHER_LINE = "We should protect our family today."
MOTHER_LINE = "I agree, and it is simpler than I feared."
NARRATOR_LINE = "Cover that grows with your family."
APPROVED = [
    {"speaker": "Father", "text": FATHER_LINE},
    {"speaker": "Mother", "text": MOTHER_LINE},
]


def _check(scene_lines, approved=APPROVED, names=("Father", "Mother")):
    return check_dialogue(scene_lines=scene_lines, approved_lines=approved, character_names=names)


def _compare(transcript, approved=APPROVED):
    return unexpected_speech(transcript, [line["text"] for line in approved])


# ------------------------------------------------------------------ the contract
def test_a_swapped_speaker_is_refused():
    problems = _check([
        {"speaker": "Mother", "text": FATHER_LINE},
        {"speaker": "Father", "text": MOTHER_LINE},
    ])
    assert any("approved for father" in p.lower() for p in problems)


def test_invented_dialogue_is_refused():
    problems = _check([*APPROVED, {"speaker": "Father", "text": "Call us on 1800-PRU now!"}])
    assert any("was not approved" in p for p in problems)


def test_paraphrased_dialogue_is_refused():
    problems = _check([{"speaker": "Father", "text": "We should protect the family."}])
    assert any("was not approved" in p for p in problems)


def test_a_line_with_no_speaker_is_refused():
    assert any("no speaker" in p for p in _check([{"speaker": "", "text": FATHER_LINE}]))


def test_a_speaker_who_is_not_in_the_scene_is_refused():
    problems = _check([{"speaker": "Neighbour", "text": FATHER_LINE}])
    assert any("not a character in this scene" in p for p in problems)


def test_a_dropped_line_is_reported_across_the_video():
    said = [{"speaker": "Father", "text": FATHER_LINE}]
    assert [x["text"] for x in missing_from_scenes(APPROVED, said)] == [MOTHER_LINE]
    assert missing_from_scenes(APPROVED, APPROVED) == []


def test_presentation_differences_are_not_treated_as_changes():
    assert normalise_line(f'  "{FATHER_LINE}"  ') == FATHER_LINE
    assert normalise_line("“We should protect  our family today.”") == FATHER_LINE
    assert _check([{"speaker": "Father", "text": f'"{FATHER_LINE}"'}]) == []


def test_a_speaker_named_with_an_article_still_matches():
    assert _check([{"speaker": "The Father", "text": FATHER_LINE}], names=["The Father"]) == []


def test_lines_out_of_order_are_refused():
    problems = _check(list(reversed(APPROVED)))
    assert any("out of order" in p for p in problems)


def test_quote_style_does_not_defeat_the_allocation_check():
    assert check_allocation(
        scene_lines=[{"speaker": "Mother", "text": MOTHER_LINE}],
        other_scene_lines=[{"speaker": "Mother", "text": f'"{MOTHER_LINE}"'}],
    )


def test_a_narrator_is_a_valid_speaker_without_being_in_the_cast():
    for name in ("Narrator", "narrator", "Voiceover", "Voice-over", "VO"):
        assert is_disembodied(name), name
    assert not is_disembodied("Father")
    assert not is_disembodied("Narinder")
    approved = [*APPROVED, {"speaker": "Narrator", "text": NARRATOR_LINE}]
    assert _check(approved, approved=approved) == []


# ------------------------------------------------------------------ vocalisation guard
def test_an_unapproved_vocalisation_instruction_is_refused():
    assert check_vocalisations("ambience", "children giggling in the next room")
    assert any("quoted speech" in p for p in check_vocalisations("sfx", 'someone calls "hello"'))


@pytest.mark.parametrize("ambience", [
    "crystal glassware clinking on the counter",
    "human presence, someone using the kitchen tap",
    "a sober, considered atmosphere",
    "out of sight behind the doorway",
    "the speaker system is switched off",
])
def test_ordinary_sound_notes_are_not_mistaken_for_vocalisation(ambience):
    assert check_vocalisations("ambience", ambience) == []


# ------------------------------------------------------------------ transcript diff
def test_exact_character_dialogue_passes():
    result = _compare(f"{FATHER_LINE} {MOTHER_LINE}")
    assert result["extra"] == [] and result["missing"] == []


def test_missing_and_garbled_narration_is_reported():
    narrator = [{"speaker": "Narrator", "text": NARRATOR_LINE}]
    assert _compare("NO_SPEECH", narrator)["missing"] == [NARRATOR_LINE]
    garbled = _compare("Da da da.", narrator)
    assert garbled["missing"] == [NARRATOR_LINE]
    assert garbled["extra"] == ["da da da"]


@pytest.mark.parametrize("transcript", [
    "its time we set up the family health cover",
    "It’s time we set up the family health cover.",
    "  IT'S TIME WE SET UP THE FAMILY HEALTH COVER!  ",
])
def test_transcription_punctuation_and_contractions_are_not_changes(transcript):
    father = [{"speaker": "Father", "text": "It's time we set up the family health cover."}]
    result = _compare(transcript, father)
    assert result["extra"] == [] and result["missing"] == []


@pytest.mark.parametrize("transcript", [
    "It's time we set up the family health cover. Uh.",
    "It's time we, uh, set up the family health cover.",
    "Erm. It's time we set up the family health cover.",
])
def test_hesitation_sounds_are_not_unapproved_speech(transcript):
    father = [{"speaker": "Father", "text": "It's time we set up the family health cover."}]
    result = _compare(transcript, father)
    assert result["extra"] == [] and result["missing"] == []


def test_filler_stripping_does_not_excuse_invented_words():
    father = [{"speaker": "Father", "text": "It's time we set up the family health cover."}]
    assert _compare("It's time we set up the family health cover. Uh, its just easy.",
                    father)["extra"] == ["its just easy"]
    assert _compare("Uh. Um.", father)["missing"] == [father[0]["text"]]


def test_extra_words_fail():
    assert _compare(f"Mama. {FATHER_LINE} {MOTHER_LINE}")["extra"] == ["mama"]


def test_another_scenes_dialogue_fails():
    result = _compare(f"{FATHER_LINE} {MOTHER_LINE}", [{"speaker": "Father", "text": FATHER_LINE}])
    assert result["extra"] and "agree" in result["extra"][0]


def test_a_line_delivered_twice_fails():
    narrator = [{"speaker": "Narrator", "text": NARRATOR_LINE}]
    assert _compare(f"{NARRATOR_LINE} {NARRATOR_LINE}", narrator)["extra"]


def test_transcriber_noise_markers_are_not_speech():
    father = [{"speaker": "Father", "text": FATHER_LINE}]
    assert _compare(f"{FATHER_LINE} [inaudible] (music)", father)["extra"] == []


def test_a_transcribers_note_about_a_breath_is_not_speech():
    approved = "Don't worry about the bills, Papa. Just rest now."
    for noise in ("[sigh]", "(laughs)", "*coughs*", "sigh", "laughs"):
        assert speech_words(f"{approved} {noise}") == speech_words(approved), noise


# ------------------------------------------------------------------ counting in any script
HINDI = "इसलिए आज की सही तैयारी, हमें कल के लिए निश्चिंत रख सकती है।"


def test_a_hindi_line_is_counted_by_words():
    assert count_words(HINDI) == 13
    assert len(words(HINDI)) == 13
    assert words(HINDI) != words("कोई और पंक्ति है")


def test_the_language_is_read_off_the_words():
    assert dialogue_language([{"text": "आज की सही तैयारी"}]) == "Hindi"
    assert dialogue_language([{"text": "We took care of that."}]) == "English"
    assert dialogue_language([]) == "English"


def test_english_counting_is_unchanged():
    assert count_words("We took care of that long ago.") == 7
    assert words("it's") == ["its"]


# ------------------------------------------------------------------ sizing
@pytest.mark.parametrize("seconds", [4.0, 5.0, 6.0, 8.0])
def test_a_shorter_scene_allows_fewer_words(seconds):
    assert speakable_words(seconds) == int(seconds * 0.8 * 2.5)
    assert speakable_words(seconds) > speakable_words(seconds - 1)


def test_the_budget_is_a_range_and_not_only_a_ceiling():
    for seconds in (4.0, 6.0, 8.0):
        assert 0 < speakable_words_floor(seconds) < speakable_words(seconds)
    assert (speakable_words_floor(4.0), speakable_words(4.0)) == (6, 8)


def test_a_line_inside_the_budget_passes_and_one_over_is_refused():
    fits = {"speaker": "A", "text": " ".join(["word"] * speakable_words(6.0))}
    assert check_dialogue_fits([fits], 6.0, max_scene_seconds=10) == []
    over = {"speaker": "A", "text": " ".join(["word"] * (speakable_words(4.0) + 6))}
    assert check_dialogue_fits([over], 4.0, max_scene_seconds=10) != []


def test_the_fit_remedy_knows_when_the_scene_cannot_grow():
    over = {"speaker": "A", "text": " ".join(["word"] * 40)}
    assert "already at the 10s maximum" in check_dialogue_fits([over], 10.0, 10.0)[0]


def test_speech_rate_is_configurable():
    line = [{"text": "one two three four five six"}]
    assert speech_seconds(line) == pytest.approx(2.4)
    assert speech_seconds(line, SpeechRate(words_per_second=3.0)) == pytest.approx(2.0)


# ------------------------------------------------------------------ ported planner
def test_durations_balance_to_the_target():
    assert plan_scene_durations(22, 4, [4, 6, 8]) == [6.0, 6.0, 6.0, 4.0]


def test_dialogue_that_fits_nowhere_is_reported_rather_than_shuffled_quietly():
    long_line = "Knowing that we were fully protected gave us the clarity to focus on him"
    scenes = [
        {"title": "A", "duration_seconds": 4.0, "dialogue_lines": [{"text": long_line}]},
        {"title": "B", "duration_seconds": 4.0, "dialogue_lines": [{"text": long_line}]},
    ]
    assert match_durations_to_dialogue(scenes) == ["A", "B"]


def test_longer_lines_get_longer_scenes_without_moving_words():
    scenes = [
        {"title": "A", "duration_seconds": 8.0, "dialogue_lines": [{"text": "Short."}]},
        {"title": "B", "duration_seconds": 4.0,
         "dialogue_lines": [{"text": "one two three four five six seven eight nine ten"}]},
    ]
    assert match_durations_to_dialogue(scenes) == []
    assert [s["duration_seconds"] for s in scenes] == [4.0, 8.0]
