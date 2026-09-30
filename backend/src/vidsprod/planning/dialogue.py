# Ported from phi-service-layer/media-generation/backend/app/application/scene_spec.py
# (2026-09-29). Kept: the dialogue contract (exact words, speaker, order, one scene per
# line), the vocalisation guard, speech sizing and the transcript comparison — each proven
# against real generated media. Changed: speech rate, dialogue share and the maximum clip
# length are parameters (they came from Veo constants) and the Veo-specific prompt
# rendering was not ported.
"""The dialogue contract and speech timing.

Every approved line is spoken exactly once, by the speaker it was approved for, in the
approved order, with nothing added. These checks are deterministic on purpose: the point
is to stop trusting a model's judgement about its own output.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class SpeechRate:
    """Configurable speech-rate assumptions (Settings.speech_*)."""

    words_per_second: float = 2.5
    dialogue_share: float = 0.8  # how much of a scene speech may occupy
    min_fill_share: float = 0.75  # the least of that room a line should use


DEFAULT_RATE = SpeechRate()


def normalise_line(text: str) -> str:
    """A line reduced to what must match: quote style and whitespace are presentation."""
    cleaned = str(text or "").strip()
    cleaned = cleaned.replace("“", '"').replace("”", '"')
    cleaned = cleaned.replace("‘", "'").replace("’", "'")
    cleaned = cleaned.strip().strip('"').strip()
    return re.sub(r"\s+", " ", cleaned)


_DISEMBODIED = frozenset(
    (
        "narrator", "narration", "voiceover", "voice over", "voice-over", "vo", "v o",
        "voice", "commentary", "commentator", "announcer", "narrator vo", "vo artist",
    )
)
_DISEMBODIED_STEMS = ("narrat", "voice over", "voiceover", "comment")
_NARRATOR_STEMS = ("narrat", "comment")
_CANONICAL_VOICES = {"NARRATOR": "Narrator", "VOICEOVER": "Voiceover"}


def speaker_key(name: str) -> str:
    """A speaker name reduced for comparison: "The Father" matches "father"."""
    cleaned = re.sub(r"[^a-z0-9 ]+", "", str(name or "").lower()).strip()
    cleaned = re.sub(r"^(the|a|an)\s+", "", cleaned)
    return re.sub(r"\s+", " ", cleaned)


def is_disembodied(speaker: str) -> bool:
    """Heard rather than seen. Deliberately narrow: "Narinder" is not a narrator."""
    key = speaker_key(speaker)
    if not key:
        return False
    if key in _DISEMBODIED:
        return True
    first = key.split(" ", 1)[0]
    return any(first.startswith(stem) or key.startswith(stem) for stem in _DISEMBODIED_STEMS)


def dialogue_kind(speaker: str) -> str:
    key = speaker_key(speaker)
    if not is_disembodied(key):
        return "CHARACTER"
    first = key.split(" ", 1)[0]
    if any(key.startswith(stem) or first.startswith(stem) for stem in _NARRATOR_STEMS):
        return "NARRATOR"
    return "VOICEOVER"


def canonical_speaker(speaker: str) -> str:
    """Unseen voices collapse onto one name per kind; characters keep theirs."""
    name = str(speaker or "").strip()
    if not is_disembodied(name):
        return name
    return _CANONICAL_VOICES.get(dialogue_kind(name), "Voiceover")


def check_dialogue(
    *,
    scene_lines: Sequence[dict],
    approved_lines: Sequence[dict],
    character_names: Sequence[str],
    strict_speakers: bool = True,
) -> list[str]:
    """Every way a scene's dialogue can fail to be the approved dialogue."""
    problems: list[str] = []
    approved_by_text: dict[str, list[str]] = {}
    for line in approved_lines:
        key = normalise_line(line.get("text", ""))
        if key:
            approved_by_text.setdefault(key, []).append(speaker_key(line.get("speaker", "")))

    known = {speaker_key(name) for name in character_names if speaker_key(name)}
    known |= {
        speaker_key(line.get("speaker", ""))
        for line in approved_lines
        if speaker_key(line.get("speaker", ""))
    }
    seen: set[str] = set()

    for index, line in enumerate(scene_lines, start=1):
        text = normalise_line(line.get("text", ""))
        speaker = str(line.get("speaker") or "").strip()
        if not text:
            problems.append(f"line {index} has no text")
            continue
        if not speaker:
            problems.append(f'line {index} ("{text[:40]}") has no speaker assigned')
            continue
        if text in seen:
            problems.append(f'line {index} repeats "{text[:40]}" already spoken in this scene')
        seen.add(text)

        if approved_by_text:
            if text not in approved_by_text:
                problems.append(
                    f'line {index} was not approved: "{text[:60]}" does not appear in the '
                    "approved script"
                )
                continue
            expected = [s for s in approved_by_text[text] if s]
            if expected and speaker_key(speaker) not in expected:
                problems.append(
                    f'line {index} "{text[:40]}" is assigned to {speaker} but was approved '
                    f"for {expected[0]}"
                )

        if (
            strict_speakers
            and known
            and not is_disembodied(speaker)
            and speaker_key(speaker) not in known
        ):
            problems.append(
                f"line {index} is assigned to {speaker}, who is not a character in this "
                f"scene ({', '.join(sorted(known))}). A line meant to be heard rather "
                "than seen must be assigned to Narrator or Voiceover"
            )

    if approved_by_text:
        approved_order = [normalise_line(line.get("text", "")) for line in approved_lines]
        positions = [
            approved_order.index(normalise_line(line.get("text", "")))
            for line in scene_lines
            if normalise_line(line.get("text", "")) in approved_order
        ]
        if positions != sorted(positions):
            problems.append(
                "the lines are out of order: this scene delivers them in a different "
                "sequence from the approved script"
            )
    return problems


def check_allocation(*, scene_lines: Sequence[dict], other_scene_lines: Sequence[dict]) -> list[str]:
    """Lines this scene says that belong to a different scene."""
    elsewhere = {normalise_line(line.get("text", "")) for line in other_scene_lines}
    elsewhere.discard("")
    problems = []
    for index, line in enumerate(scene_lines, start=1):
        text = normalise_line(line.get("text", ""))
        if text and text in elsewhere:
            problems.append(
                f'line {index} "{text[:50]}" is already allocated to another scene; '
                "each approved line belongs to exactly one scene"
            )
    return problems


def missing_from_scenes(approved_lines: Sequence[dict], all_scene_lines: Sequence[dict]) -> list:
    """Approved lines that no scene says. Whole-video check."""
    said = {normalise_line(line.get("text", "")) for line in all_scene_lines}
    return [
        line
        for line in approved_lines
        if normalise_line(line.get("text", "")) and normalise_line(line["text"]) not in said
    ]


# Sounds that would put unapproved speech in the clip. Whole words, enumerated forms:
# `cry\w*` matches "crystal", `sigh\w*` matches "sight".
_VOCALISATION = re.compile(
    r"\b("
    r"laugh|laughs|laughing|laughter|giggle|giggles|giggling|"
    r"chuckle|chuckles|chuckling|cry|cries|crying|cried|sob|sobs|sobbing|"
    r"weep|weeps|weeping|hum|hums|humming|sing|sings|singing|"
    r"shout|shouts|shouting|scream|screams|screaming|gasp|gasps|gasping|"
    r"sigh|sighs|sighing|murmur|murmurs|murmuring|mutter|mutters|muttering|"
    r"babble|babbles|babbling|chatter|chatters|chattering|"
    r"greet|greets|greeting|say|says|saying|speaks|speaking|talks|talking|"
    r"narrator|narration|narrates|voiceover|voice-over"
    r")\b",
    re.IGNORECASE,
)


def check_vocalisations(label: str, text: str) -> list[str]:
    """A prose field (visual description, ambience) that smuggles in speech."""
    problems = []
    found = _VOCALISATION.search(text or "")
    if found:
        problems.append(f"{label} asks for an unapproved vocalisation ({found.group(0)!r})")
    if '"' in (text or "") or "“" in (text or ""):
        problems.append(f"{label} contains quoted speech, which belongs in the script")
    return problems


# ------------------------------------------------------------------ counting words
_COMBINING_MARKS = "".join(
    chr(code) for code in range(0x0300, 0x10000) if unicodedata.category(chr(code)) in ("Mn", "Mc")
)
_WORD_RE = re.compile(rf"[\w'{re.escape(_COMBINING_MARKS)}]+")

_SCRIPT_LANGUAGES = (
    ((0x0900, 0x097F), "Hindi"),
    ((0x0980, 0x09FF), "Bengali"),
    ((0x0A00, 0x0A7F), "Punjabi"),
    ((0x0A80, 0x0AFF), "Gujarati"),
    ((0x0B00, 0x0B7F), "Odia"),
    ((0x0B80, 0x0BFF), "Tamil"),
    ((0x0C00, 0x0C7F), "Telugu"),
    ((0x0C80, 0x0CFF), "Kannada"),
    ((0x0D00, 0x0D7F), "Malayalam"),
)
DEFAULT_LANGUAGE = "English"


def dialogue_language(lines: Sequence[dict]) -> str:
    """The language the lines are written in, read off their script, not asserted."""
    counts: dict[str, int] = {}
    for line in lines or ():
        for char in str(line.get("text") or ""):
            if not char.isalpha():
                continue
            code = ord(char)
            for (low, high), language in _SCRIPT_LANGUAGES:
                if low <= code <= high:
                    counts[language] = counts.get(language, 0) + 1
                    break
            else:
                counts[DEFAULT_LANGUAGE] = counts.get(DEFAULT_LANGUAGE, 0) + 1
    if not counts:
        return DEFAULT_LANGUAGE
    return max(counts.items(), key=lambda item: item[1])[0]


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(str(text or "")))


def words(text: str) -> list[str]:
    lowered = str(text or "").lower().replace("’", "'").replace("'", "")
    return _WORD_RE.findall(lowered)


_FILLER_TOKENS = frozenset(
    (
        "uh", "uhh", "uhhh", "um", "umm", "ummm", "uhm", "uhhuh",
        "er", "err", "erm", "hm", "hmm", "hmmm", "mm", "mmm", "mhm", "mmhmm", "huh",
        "sigh", "sighs", "sighing", "laugh", "laughs", "laughing", "chuckle", "chuckles",
        "gasp", "gasps", "cough", "coughs", "breath", "breathes", "exhales", "inhales",
    )
)
_NOISE_TOKENS = frozenset(
    (
        "no_speech", "nospeech", "inaudible", "unintelligible", "silence", "silent",
        "music", "noise", "background", "sound", "sounds", "ambient", "ambience",
    )
)
_ANNOTATION = re.compile(r"\[[^\]]*\]|\([^)]*\)|\*[^*]*\*")
NO_SPEECH = "NO_SPEECH"

TRANSCRIBE_INSTRUCTION = (
    "Transcribe every spoken word and every human vocalisation in this audio, exactly as "
    "heard, in order. Include laughter, humming, crying, babbling, muttering and background "
    "speech, writing each as the word it sounds like. Do not summarise, correct, translate or "
    "tidy anything. Output only the transcript. If there is no speech or vocalisation at all, "
    "output exactly: NO_SPEECH"
)


def speech_words(text: str) -> list[str]:
    """`words`, minus the transcriber's noises (fillers and bracketed sound notes)."""
    return [
        token for token in words(_ANNOTATION.sub(" ", str(text or "")))
        if token not in _FILLER_TOKENS
    ]


# ------------------------------------------------------------------ sizing
def speech_seconds(lines: Sequence[dict], rate: SpeechRate = DEFAULT_RATE) -> float:
    total = sum(count_words(line.get("text")) for line in lines or [])
    return total / rate.words_per_second if total else 0.0


def speakable_words(duration_seconds: float, rate: SpeechRate = DEFAULT_RATE) -> int:
    if duration_seconds <= 0:
        return 0
    return max(1, int(duration_seconds * rate.dialogue_share * rate.words_per_second))


def speakable_words_floor(duration_seconds: float, rate: SpeechRate = DEFAULT_RATE) -> int:
    """The fewest words that fill a scene without leaving dead air a generator may fill
    with invented speech."""
    most = speakable_words(duration_seconds, rate)
    return max(1, int(most * rate.min_fill_share)) if most else 0


def min_seconds_for(lines: Sequence[dict], rate: SpeechRate = DEFAULT_RATE) -> float:
    """The shortest scene these lines can be said in at a natural pace."""
    return speech_seconds(lines, rate) / rate.dialogue_share


def check_dialogue_fits(
    lines: Sequence[dict],
    duration_seconds: float,
    max_scene_seconds: float,
    rate: SpeechRate = DEFAULT_RATE,
) -> list[str]:
    """Whether the scene's speech can be said in the time it has."""
    total = sum(count_words(line.get("text")) for line in lines or [])
    if not total or duration_seconds <= 0:
        return []
    available = duration_seconds * rate.dialogue_share
    needed = total / rate.words_per_second
    if needed <= available:
        return []
    fits = int(available * rate.words_per_second)
    over = total - fits
    remedy = f"shorten it by about {over} word{'s' if over != 1 else ''}, to roughly {fits} words"
    if duration_seconds < max_scene_seconds:
        remedy += f", or give the scene more time — it may run to {max_scene_seconds:.0f}s"
    else:
        remedy += (
            f". The scene is already at the {max_scene_seconds:.0f}s maximum, so either the "
            "line is shortened or it is split across two scenes"
        )
    return [
        f"the speech is about {total} words, which takes roughly {needed:.1f}s to say at a "
        f"natural pace, and this scene is {duration_seconds:.0f}s — {remedy}"
    ]


# ------------------------------------------------------------------ transcript comparison
def unexpected_speech(transcript: str, expected: Sequence[str]) -> dict:
    """What the audio says that it should not, and what it should say but does not.

    Approved lines are consumed out of the transcript in order; leftover words with
    content are speech nobody approved. Returns {"extra", "missing", "transcript_words"}.
    """
    text = str(transcript or "").strip()
    if text.upper().replace(" ", "_") == NO_SPEECH or not text:
        return {"extra": [], "missing": [line for line in expected if words(line)],
                "transcript_words": 0}

    remaining = speech_words(text)
    missing: list[str] = []
    for line in expected:
        needle = speech_words(line)
        if not needle:
            continue
        found_at = -1
        for start in range(len(remaining) - len(needle) + 1):
            if remaining[start : start + len(needle)] == needle:
                found_at = start
                break
        if found_at == -1:
            missing.append(line)
        else:
            remaining = remaining[:found_at] + remaining[found_at + len(needle):]

    extra: list[str] = []
    run: list[str] = []
    for token in remaining:
        if token in _NOISE_TOKENS:
            if run:
                extra.append(" ".join(run))
                run = []
            continue
        run.append(token)
    if run:
        extra.append(" ".join(run))
    return {"extra": [p for p in extra if p], "missing": missing,
            "transcript_words": len(words(text))}
