"""The planning documents: brief, creative plan, script, scene plan, prompts.

These are the typed contracts between agents. Models that an LLM fills in are kept flat
and explicit (ids are strings, lists hold small objects) because that is what structured
output handles reliably; everything an LLM returns is validated against these models and
then checked again by deterministic rules before a stage may pass.
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from vidsprod.domain.enums import AspectRatio, ScriptLineKind, TransitionKind

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
Ident = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.\-]{1,64}$")]


class Doc(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ------------------------------------------------------------------ brief (input)
class ClaimInput(Doc):
    text: ShortText
    fact_id: Ident | None = Field(
        default=None, description="Approved fact this claim relies on (from the brand profile)"
    )


class DisclosureInput(Doc):
    text: LongText
    on_screen: bool = True
    spoken: bool = False


class OutputRequirements(Doc):
    aspect_ratio: AspectRatio = AspectRatio.LANDSCAPE
    min_duration_seconds: float | None = Field(default=None, ge=1, le=600)
    max_duration_seconds: float | None = Field(default=None, ge=1, le=600)
    language: ShortText = "English"
    captions_required: bool = True
    voiceover_required: bool = True
    end_card_required: bool = True

    @field_validator("max_duration_seconds")
    @classmethod
    def _max_after_min(cls, value: float | None, info) -> float | None:
        low = info.data.get("min_duration_seconds")
        if value is not None and low is not None and value < low:
            raise ValueError("max_duration_seconds must be at least min_duration_seconds")
        return value


class UserScene(Doc):
    """A scene the person wrote themselves. It is used word for word: no AI planning."""

    prompt: LongText = Field(description="What Google Vids should show; typed into the AI video prompt as written")
    on_screen_text: ShortText | None = Field(default=None, description="Words shown on screen during the scene")
    duration_seconds: int = Field(default=8, ge=3, le=10, description="One generated clip: 3 to 10 whole seconds")
    photo_asset_id: Ident | None = Field(
        default=None, description="One of reference_asset_ids, attached to this scene's generation as an ingredient")


class BriefInput(Doc):
    """What a person writes. The Intake agent turns it into a `CampaignBrief`."""

    raw_brief: LongText
    objective: ShortText | None = None
    audience: ShortText | None = None
    product: ShortText | None = None
    key_message: ShortText | None = None
    cta: ShortText | None = None
    tone: ShortText | None = None
    required_claims: list[ClaimInput] = Field(default_factory=list, max_length=20)
    disclosures: list[DisclosureInput] = Field(default_factory=list, max_length=10)
    mandatory_elements: list[ShortText] = Field(default_factory=list, max_length=20)
    reference_asset_ids: list[Ident] = Field(
        default_factory=list, max_length=7,
        description="Uploaded reference images (Vids documents up to 7 ingredients)")
    output: OutputRequirements = Field(default_factory=OutputRequirements)
    scenes: list[UserScene] = Field(
        default_factory=list, max_length=10,
        description="Scenes written by the person. When given, they replace AI planning")

    @model_validator(mode="after")
    def _scene_photos_are_references(self) -> BriefInput:
        known = set(self.reference_asset_ids)
        for i, scene in enumerate(self.scenes, 1):
            if scene.photo_asset_id and scene.photo_asset_id not in known:
                raise ValueError(f"scene {i}'s photo is not one of the uploaded photos")
        return self


# ------------------------------------------------------------------ brief (validated)
class CampaignBrief(Doc):
    objective: ShortText
    audience: ShortText
    product: ShortText
    key_message: ShortText
    cta: ShortText
    tone: ShortText
    claims: list[ClaimInput] = Field(default_factory=list, max_length=20)
    disclosures: list[DisclosureInput] = Field(default_factory=list, max_length=10)
    brand_rules: list[ShortText] = Field(default_factory=list, max_length=30)
    mandatory_elements: list[ShortText] = Field(default_factory=list, max_length=20)
    output: OutputRequirements
    reference_asset_ids: list[Ident] = Field(default_factory=list, max_length=7)
    open_questions: list[ShortText] = Field(
        default_factory=list,
        description="Anything the brief leaves ambiguous that a reviewer should settle",
        max_length=10,
    )


# ------------------------------------------------------------------ creative plan
class Character(Doc):
    id: Ident
    name: ShortText
    role: ShortText
    appearance: LongText = Field(description="Age, build, face, hair: what must stay constant")
    wardrobe: ShortText


class Location(Doc):
    id: Ident
    name: ShortText
    description: LongText


class Beat(Doc):
    id: Ident
    name: ShortText
    purpose: ShortText = Field(description="What this beat does for the viewer")
    emotion: ShortText


class CreativePlan(Doc):
    title: ShortText
    logline: ShortText
    concept: LongText
    emotional_direction: ShortText
    visual_direction: LongText = Field(description="Look, lighting, camera language, colour")
    beats: list[Beat] = Field(min_length=2, max_length=12)
    characters: list[Character] = Field(default_factory=list, max_length=6)
    locations: list[Location] = Field(min_length=1, max_length=8)
    objective_beat_id: Ident = Field(description="Beat that delivers the campaign objective")
    cta_beat_id: Ident = Field(description="Beat that carries the call to action")


# ------------------------------------------------------------------ script
class ScriptLine(Doc):
    id: Ident
    beat_id: Ident
    kind: ScriptLineKind
    speaker: ShortText = Field(description="'Narrator' for voiceover, else a character name")
    text: LongText
    fact_ids: list[Ident] = Field(
        default_factory=list, description="Approved facts any claim in this line relies on"
    )


class Super(Doc):
    """On-screen text."""

    id: Ident
    beat_id: Ident
    text: ShortText
    purpose: ShortText


class Script(Doc):
    lines: list[ScriptLine] = Field(min_length=1, max_length=40)
    supers: list[Super] = Field(default_factory=list, max_length=20)
    cta_line_id: Ident
    disclosure_texts: list[LongText] = Field(default_factory=list, max_length=10)


# ------------------------------------------------------------------ scene plan
class ScenePlanEntry(Doc):
    scene_key: Annotated[str, StringConstraints(pattern=r"^S\d{2}$")]
    order: int = Field(ge=1)
    beat_id: Ident
    narrative_purpose: ShortText
    duration_seconds: float = Field(gt=0)
    visual_description: LongText
    camera_direction: ShortText
    lighting: ShortText
    character_ids: list[Ident] = Field(default_factory=list)
    location_id: Ident
    line_ids: list[Ident] = Field(default_factory=list)
    super_ids: list[Ident] = Field(default_factory=list)
    transition_out: TransitionKind = TransitionKind.CUT
    depends_on: list[Annotated[str, StringConstraints(pattern=r"^S\d{2}$")]] = Field(
        default_factory=list, description="Earlier scenes this one must stay continuous with"
    )
    validation_criteria: list[ShortText] = Field(min_length=1, max_length=12)


class ScenePlan(Doc):
    total_duration_seconds: float = Field(gt=0)
    scenes: list[ScenePlanEntry] = Field(min_length=1, max_length=40)
    rationale: LongText


class SceneDesign(Doc):
    """The LLM's visual design for one scene skeleton the planner fixed."""

    scene_key: Annotated[str, StringConstraints(pattern=r"^S\d{2}$")]
    visual_description: LongText
    camera_direction: ShortText
    lighting: ShortText
    character_ids: list[Ident] = Field(default_factory=list)
    location_id: Ident
    validation_criteria: list[ShortText] = Field(min_length=1, max_length=12)


class SceneDesigns(Doc):
    scenes: list[SceneDesign] = Field(min_length=1, max_length=40)


# ------------------------------------------------------------------ prompts
class VidsPrompt(Doc):
    scene_key: str
    prompt: LongText
    duration_seconds: float
    aspect_ratio: AspectRatio
    continuity_tokens: list[str] = Field(
        default_factory=list, description="Phrases that must appear verbatim for continuity"
    )
    spoken_lines: list[str] = Field(default_factory=list)
    reference_asset_ids: list[str] = Field(default_factory=list)


def words(text: str) -> list[str]:
    return re.findall(r"[\w']+", text.lower())
