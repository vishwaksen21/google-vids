# Ported from phi-service-layer/media-generation/backend/tests/test_brand.py (pure tests only;
# the application-wiring tests at the end of the original test that project's container).
"""Brand context: DESIGN.md is parsed, projected per stage, injected into every prompt,
and checked before generation and before the final render.

The tests that matter most here are the ones asserting the *scope* of the colour rules:
DESIGN.md is written for UI, so a naive reading forbids blue and green, and applied to a
family in a park that would forbid the sky and the grass. Brand colour rules govern
brand-controlled colour only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vidsprod.brand.alignment import BrandAlignmentChecker
from vidsprod.brand.design_document import load_brand_guidelines, parse_design_document
from vidsprod.brand.guidelines import BrandGuidelines, ColourToken
from vidsprod.brand.projections import COLOUR_SCOPE_NOTE, BrandStage, project
from vidsprod.brand.style import BrandStyle
from vidsprod.domain.enums import Severity

# vids/backend/tests/unit -> media-generation/DESIGN.md
DESIGN_MD = Path(__file__).resolve().parents[4] / "DESIGN.md"


# --------------------------------------------------------------------------- parsing
def test_the_real_design_document_parses():
    guidelines = load_brand_guidelines(DESIGN_MD)

    assert guidelines.available
    assert guidelines.source_digest and len(guidelines.source_digest) == 64
    assert guidelines.palette_hexes == ("#E71D2D", "#666D71", "#FFFFFF")
    assert guidelines.primary.normalised == "#E71D2D"
    assert guidelines.neutral.normalised == "#666D71"


def test_the_forbidden_colours_are_exactly_the_documented_ones():
    """A regression guard: an earlier parse drifted into an unrelated list further down
    the document and picked up 'White backgrounds' and 'Clean geometry' as forbidden."""
    guidelines = load_brand_guidelines(DESIGN_MD)

    assert guidelines.forbidden_colours == (
        "Blue",
        "Purple",
        "Green",
        "Orange",
        "Pink",
        "Cyan",
        "Neon colors",
    )


def test_identity_appearance_typography_and_rules_are_extracted():
    guidelines = load_brand_guidelines(DESIGN_MD)

    assert "Trust" in guidelines.identity_values
    assert "Professionalism" in guidelines.identity_values
    assert "Clean" in guidelines.appearance
    assert guidelines.typography[0] == "Inter"
    assert any("distort" in rule.lower() for rule in guidelines.logo_rules)
    assert any("cyberpunk" in style.lower() for style in guidelines.avoid_styles)
    assert "Accessibility" in guidelines.design_principles


def test_a_missing_design_document_degrades_instead_of_inventing_a_brand(tmp_path):
    guidelines = load_brand_guidelines(tmp_path / "nope.md")

    assert guidelines.available is False
    assert guidelines.source_digest is None
    assert guidelines.palette == ()
    # And a projection over it is empty, so nothing is injected into any prompt.
    assert project(guidelines, BrandStage.STORYLINE).as_prompt_block() == ""


def test_a_renamed_heading_loses_only_that_facet():
    text = DESIGN_MD.read_text(encoding="utf-8").replace("## Logo Rules", "## Marque Rules")
    guidelines = parse_design_document(text)

    assert guidelines.available
    assert guidelines.palette_hexes  # unaffected
    assert guidelines.logo_rules == ()  # the renamed section, and only that


def test_the_parse_is_pure_and_stable():
    text = DESIGN_MD.read_text(encoding="utf-8")

    assert parse_design_document(text) == parse_design_document(text)


# --------------------------------------------------------------------- colour compat
@pytest.mark.parametrize(
    "hex_value,expected",
    [
        ("#E71D2D", True),  # the brand red itself
        ("#C4121F", True),  # a darker derived red
        ("#F5A0A8", True),  # a lighter derived red
        ("#FFFFFF", True),  # white
        ("#666D71", True),  # the brand grey
        ("#1A1A1A", True),  # near-black reads as a neutral
        ("#0000FF", False),
        ("#00A651", False),
        ("#7B2FBE", False),
        ("#FF9900", False),
        ("#39FF14", False),  # neon green
    ],
)
def test_brand_compatibility_allows_derived_shades_and_rejects_unrelated_hues(
    hex_value, expected
):
    """Hue, not RGB distance: near-black is numerically far from every palette entry yet
    is a perfectly good derived neutral, while a saturated blue can sit closer."""
    guidelines = load_brand_guidelines(DESIGN_MD)

    assert guidelines.is_brand_compatible(hex_value) is expected


def test_compatibility_is_unanswerable_without_a_palette():
    assert BrandGuidelines.unavailable().is_brand_compatible("#E71D2D") is None


def test_colour_token_rejects_nonsense_and_normalises():
    assert ColourToken("x", "#fff").normalised == "#FFFFFF"
    assert ColourToken("x", "#E71D2D").rgb == (231, 29, 45)
    with pytest.raises(ValueError):
        ColourToken("x", "not-a-colour")


# ------------------------------------------------------------------------ projections
def test_every_stage_has_a_projection_and_none_leak_the_whole_document():
    guidelines = load_brand_guidelines(DESIGN_MD)
    whole_document_size = len(DESIGN_MD.read_text(encoding="utf-8"))

    for stage in BrandStage:
        block = project(guidelines, stage, brand_name="Prudential").as_prompt_block()
        assert block, f"{stage} has no brand context"
        # A projection is a slice, not the design system. If this ever approaches the
        # document's size, someone has started dumping DESIGN.md into prompts.
        assert len(block) < whole_document_size / 4


def test_text_stages_get_identity_and_tone_not_a_palette():
    guidelines = load_brand_guidelines(DESIGN_MD)

    block = project(
        guidelines, BrandStage.STORYLINE, brand_name="Prudential", brand_tone="warm"
    ).as_prompt_block()

    assert "Prudential" in block
    assert "Trust" in block
    assert "warm" in block
    assert "#E71D2D" not in block  # a storyline has no colours to set


def test_visual_stages_get_the_palette_and_the_scope_note():
    guidelines = load_brand_guidelines(DESIGN_MD)

    block = project(guidelines, BrandStage.SCENE_GENERATION).as_prompt_block()

    assert "#E71D2D" in block
    assert COLOUR_SCOPE_NOTE in block
    # The scope note is the whole reason generated media stays sane.
    assert "sky, foliage, skin" in block


def test_only_the_stages_that_place_a_logo_get_the_logo_rules():
    """Composition places the mark. No generator does, so no generator is told how to.

    The logo-usage rules are handling instructions for the real asset. Given to a model
    that is drawing a picture, they are read as a brief to draw a logo — observed live:
    an advertising photograph that never mentioned branding came back with an invented
    "PRUDENTIAL" wordmark in the corner and fabricated brand text on a tablet in the shot.

    Adding a "do not draw a logo" sentence alongside them did not fix it; the model drew
    one anyway with both instructions in the same prompt. IMAGE_GENERATION had them and
    SCENE_GENERATION did not, which is exactly why only the image path ever produced a
    fake mark.
    """
    guidelines = load_brand_guidelines(DESIGN_MD)

    def block(stage):
        return project(guidelines, stage).as_prompt_block()

    assert "Logo usage" in block(BrandStage.COMPOSITION)
    for generative in (
        BrandStage.IMAGE_GENERATION,
        BrandStage.SCENE_GENERATION,
        BrandStage.REFERENCE_IMAGE,
        BrandStage.STORYLINE,
    ):
        assert "Logo usage" not in block(generative), generative


def test_the_projection_records_its_provenance():
    guidelines = load_brand_guidelines(DESIGN_MD)

    provenance = project(guidelines, BrandStage.SCENE_GENERATION, brand_name="P").provenance()

    assert provenance["design_digest"] == guidelines.source_digest
    assert provenance["palette"] == ["#E71D2D", "#666D71", "#FFFFFF"]
    assert provenance["brand_stage"] == "SCENE_GENERATION"


# -------------------------------------------------------------------------- alignment
@pytest.fixture
def checker_and_projection():
    guidelines = load_brand_guidelines(DESIGN_MD)
    return BrandAlignmentChecker(), project(guidelines, BrandStage.SCENE_GENERATION)


def test_natural_colour_in_a_scene_is_not_a_brand_violation(checker_and_projection):
    """THE scope test. DESIGN.md says 'no blue'; it means no blue *brand* colour."""
    checker, projection = checker_and_projection

    for benign in (
        "She wears a blue coat in the park",
        "A wide shot of green fields under a blue sky",
        "The father holds an orange while the child laughs",
    ):
        assert checker.check_text(benign, projection) == [], benign


def test_a_forbidden_colour_used_as_brand_colour_is_flagged(checker_and_projection):
    checker, projection = checker_and_projection

    findings = checker.check_text("Use a blue brand accent on the end card", projection)

    assert [f.code for f in findings] == ["brand_colour_conflict"]
    assert findings[0].severity is Severity.MINOR
    assert "#E71D2D" in findings[0].recommendation


def test_an_off_brand_aesthetic_is_flagged(checker_and_projection):
    checker, projection = checker_and_projection

    findings = checker.check_text("A neon cyberpunk cityscape", projection)

    codes = [f.code for f in findings]
    assert codes.count("brand_style_conflict") == 2  # neon, and cyberpunk
    assert all(f.severity is Severity.MINOR for f in findings)


def test_gradients_are_noted_but_only_as_information(checker_and_projection):
    checker, projection = checker_and_projection

    findings = checker.check_text("A soft gradient behind the logo", projection)

    gradient = [f for f in findings if f.code == "brand_gradient_discouraged"]
    assert len(gradient) == 1
    assert gradient[0].severity is Severity.INFO


def test_no_brand_context_means_no_findings():
    checker = BrandAlignmentChecker()
    empty = project(BrandGuidelines.unavailable(), BrandStage.SCENE_GENERATION)

    assert checker.check_text("A neon cyberpunk cartoon rainbow", empty) == []


# ------------------------------------------------------------------------------ style
def test_the_composition_style_is_derived_from_the_palette():
    guidelines = load_brand_guidelines(DESIGN_MD)

    style = BrandStyle.derive(guidelines)

    assert style.text_colour == "#FFFFFF"
    assert style.accent_colour == "#E71D2D"
    # The caption box keeps the brand grey's hue, pushed dark enough for white text.
    assert guidelines.is_brand_compatible(style.box_colour)
    assert style.ffmpeg_box_colour().startswith("0x")
    assert style.ffmpeg_box_colour().endswith("@0.55")


def test_every_derived_composition_colour_is_on_brand():
    guidelines = load_brand_guidelines(DESIGN_MD)
    style = BrandStyle.derive(guidelines)

    for component, hex_value in style.brand_controlled_colours():
        assert guidelines.is_brand_compatible(hex_value), component


def test_style_falls_back_to_safe_neutrals_without_a_palette():
    style = BrandStyle.derive(BrandGuidelines.unavailable())

    assert style.text_colour == "#FFFFFF"
    assert style.accent_colour is None


