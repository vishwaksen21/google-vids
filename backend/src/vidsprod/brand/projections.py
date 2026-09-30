# Ported from phi-service-layer/media-generation/backend/app/brand (2026-09-29).
"""Per-stage brand projections.

Every generation stage consults the brand context, but no stage should receive the whole
of `DESIGN.md`. Dumping the entire design system into every prompt wastes context, buries
the rules that matter for the stage at hand, and makes the output harder to attribute.

So each stage gets a **projection**: a small, deterministic, reviewable slice.

    storyline            -> identity, tone, what the brand must never feel like
    scene breakdown      -> tone + aesthetic
    entity identification-> aesthetic (so the cast is plausible for this brand)
    reference image      -> palette, aesthetic, photography rules
    image generation     -> palette, aesthetic, photography rules, logo rules
    scene generation     -> palette, aesthetic, photography rules
    composition          -> palette + logo rules (overlays, captions, end card, lockup)
    validation           -> the machine-checkable rules only

Projections are pure functions of (guidelines, brand row, stage). They are deterministic
and diffable, which is why this is a projection and not a retrieval step: with one brand
document, retrieval would add non-determinism and buy nothing.

## The scope rule

`DESIGN.md` is written for UI. Read literally, "do not use blue or green" would forbid
sky and grass. Applied to generated media the colour rules govern **brand-controlled
colour only** — accent colour, graphics, overlays, captions, end cards, on-screen UI and
the logo lockup — and never natural scene colour. Every visual projection says so
explicitly in the prompt block, so the model is steered rather than confused.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from vidsprod.brand.guidelines import BrandGuidelines


class BrandStage(StrEnum):
    """The generation stages that consult brand context."""

    STORYLINE = "STORYLINE"
    SCENE_BREAKDOWN = "SCENE_BREAKDOWN"
    ENTITY_IDENTIFICATION = "ENTITY_IDENTIFICATION"
    REFERENCE_IMAGE = "REFERENCE_IMAGE"
    IMAGE_GENERATION = "IMAGE_GENERATION"
    SCENE_GENERATION = "SCENE_GENERATION"
    COMPOSITION = "COMPOSITION"
    VALIDATION = "VALIDATION"


_TEXT_STAGES = frozenset(
    {BrandStage.STORYLINE, BrandStage.SCENE_BREAKDOWN, BrandStage.ENTITY_IDENTIFICATION}
)
_VISUAL_STAGES = frozenset(
    {
        BrandStage.REFERENCE_IMAGE,
        BrandStage.IMAGE_GENERATION,
        BrandStage.SCENE_GENERATION,
        BrandStage.COMPOSITION,
    }
)

# Said verbatim in every visual prompt so the palette rule is not misread as a ban on
# natural colour. This sentence is the whole reason generated media stays sane.
COLOUR_SCOPE_NOTE = (
    "These colour rules govern brand-controlled colour only - accent colour, graphics, "
    "text overlays, captions, end cards, on-screen UI and the logo lockup. They do not "
    "govern natural colour in the scene: sky, foliage, skin, hair, clothing and product "
    "materials should look real."
)


@dataclass(frozen=True)
class BrandProjection:
    """The slice of brand context one generation stage receives."""

    stage: BrandStage
    brand_name: str | None = None
    brand_tone: str | None = None
    lines: tuple[str, ...] = field(default_factory=tuple)
    palette_hexes: tuple[str, ...] = field(default_factory=tuple)
    forbidden_colours: tuple[str, ...] = field(default_factory=tuple)
    avoid_styles: tuple[str, ...] = field(default_factory=tuple)
    source_digest: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.lines and not self.brand_name and not self.brand_tone

    def as_prompt_block(self) -> str:
        """The text injected into a prompt. Empty string when there is no brand context,
        so callers can append unconditionally without emitting a stray heading."""
        if self.is_empty:
            return ""
        head = "BRAND CONTEXT (must be honoured)"
        if self.brand_name:
            head += f" - {self.brand_name}"
        return "\n".join([head + ":", *(f"- {line}" for line in self.lines)])

    def provenance(self) -> dict:
        """Recorded alongside the generation so the brand basis is auditable."""
        return {
            "brand_stage": self.stage.value,
            "brand_name": self.brand_name,
            "design_digest": self.source_digest,
            "palette": list(self.palette_hexes),
        }


def _join(values: Sequence[str], limit: int) -> str:
    return ", ".join(values[:limit])


def project(
    guidelines: BrandGuidelines,
    stage: BrandStage,
    *,
    brand_name: str | None = None,
    brand_tone: str | None = None,
    brand_palette: str | None = None,
) -> BrandProjection:
    """Build the projection for one stage.

    `brand_name` / `brand_tone` / `brand_palette` come from the campaign's `Brand` row and
    take precedence over the document where they overlap, so a campaign can carry its own
    tone without editing `DESIGN.md`.
    """
    # With neither a design document nor a brand row there is no brand context, and the
    # honest thing is to inject nothing at all rather than generic advice dressed up as
    # brand guidance.
    if not (guidelines.available or brand_name or brand_tone or brand_palette):
        return BrandProjection(stage=stage, source_digest=guidelines.source_digest)

    lines: list = []
    palette_hexes = guidelines.palette_hexes
    if brand_palette:
        lines.append(f"Campaign palette: {brand_palette}")

    if stage in _TEXT_STAGES:
        if guidelines.identity_values:
            lines.append(
                "The brand must come across as: " + _join(guidelines.identity_values, 8) + "."
            )
        if guidelines.appearance:
            lines.append("Overall feel: " + _join(guidelines.appearance, 7) + ".")
        if brand_tone:
            lines.append(f"Brand tone of voice: {brand_tone}.")
        if stage is BrandStage.STORYLINE:
            lines.append(
                "Write with restraint and credibility - no hard sell, no hype, no "
                "exaggerated claims, nothing that could read as a guarantee."
            )
        if stage is BrandStage.ENTITY_IDENTIFICATION:
            lines.append(
                "Choose people, wardrobe, products and locations that are plausible and "
                "premium for this brand and its audience."
            )
        if guidelines.avoid_styles:
            lines.append("Never evoke: " + _join(guidelines.avoid_styles, 6) + ".")

    elif stage in _VISUAL_STAGES:
        if palette_hexes:
            described = ", ".join(t.describe() for t in guidelines.palette[:6])
            lines.append(f"Brand palette (use for brand-controlled colour only): {described}.")
        if guidelines.forbidden_colours:
            lines.append(
                "Never introduce these as brand or accent colours: "
                + _join(guidelines.forbidden_colours, 8)
                + "."
            )
        if palette_hexes or guidelines.forbidden_colours:
            lines.append(COLOUR_SCOPE_NOTE)
        if guidelines.appearance:
            lines.append("Visual character: " + _join(guidelines.appearance, 7) + ".")
        if guidelines.photography_rules and stage is not BrandStage.COMPOSITION:
            lines.append("Photography: " + _join(guidelines.photography_rules, 5) + ".")
        if guidelines.avoid_styles:
            lines.append("Avoid entirely: " + _join(guidelines.avoid_styles, 8) + ".")
        # Composition only, because composition is the only stage that places the mark.
        #
        # These rules — never distort it, never recolour it, never box it in — are written
        # for whoever puts the real logo on the picture, which is the application's own
        # overlay step. Handed to a *generator* they read as confirmation that placing one
        # is its job, and an ordinary advertising brief that never mentioned branding came
        # back with an invented "PRUDENTIAL" wordmark in the corner.
        #
        # Telling the image model not to draw one was not enough on its own: with these
        # lines still in the same prompt, it drew one anyway. The instruction had to go.
        # SCENE_GENERATION never received them, and the video path never had the problem.
        if stage is BrandStage.COMPOSITION and guidelines.logo_rules:
            lines.append("Logo usage: " + _join(guidelines.logo_rules, 6) + ".")
        if stage is BrandStage.COMPOSITION and guidelines.typography:
            lines.append("Typography for on-screen text: " + _join(guidelines.typography, 3) + ".")

    else:  # VALIDATION - machine-checkable rules only, no prose steering.
        if palette_hexes:
            lines.append("Brand-controlled colour must stay within: " + ", ".join(palette_hexes))
        if guidelines.forbidden_colours:
            lines.append("Forbidden as brand colour: " + _join(guidelines.forbidden_colours, 8))

    return BrandProjection(
        stage=stage,
        brand_name=brand_name,
        brand_tone=brand_tone,
        lines=tuple(lines),
        palette_hexes=palette_hexes,
        forbidden_colours=guidelines.forbidden_colours,
        avoid_styles=guidelines.avoid_styles,
        source_digest=guidelines.source_digest,
    )
