# Ported from phi-service-layer/media-generation/backend/app/brand (2026-09-29).
"""Brand-alignment rules.

Pure rule logic: given a brand projection and something to inspect, produce findings.
Nothing here creates `ValidationResult` rows, touches a database or calls a provider —
`app/validation/validators.py` wraps these into the validation pipeline.

Two kinds of check, and the difference matters:

* **Deterministic.** A brand-controlled colour we ourselves chose — an overlay, a
  caption box, a fade colour — is a hex value we hold, so it can be tested exactly
  against the palette. These findings are trustworthy.
* **Advisory keyword scan.** A brief that asks for "a neon cyberpunk vibe" conflicts with
  the brand before a single token is generated, and saying so early is cheap. But this is
  a keyword scan over free text, so it is *suggestive only*: it never blocks, and it is
  deliberately conservative — matching on multi-word aesthetic phrases rather than bare
  colour words, because "she wears a blue coat" is a legitimate creative choice while
  "blue corporate theme" is a brand violation.

The point of the pre-generation check is not enforcement. It is to tell the user, before
they spend a generation, that what they asked for fights the brand — while the projection
injected into the prompt does the actual steering.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from vidsprod.brand.projections import BrandProjection
from vidsprod.domain.enums import Severity


@dataclass(frozen=True)
class BrandFinding:
    """One brand observation. Mirrors the shape the UI renders for every finding."""

    code: str
    severity: Severity
    message: str
    affected_component: str
    recommendation: str
    details: dict | None = None


# Aesthetics DESIGN.md rules out. Matched as phrases so ordinary creative language is not
# flagged: "cyberpunk" is a violation, "punk band" is not.
_STYLE_TERMS = (
    "cyberpunk",
    "neon",
    "glassmorphism",
    "glassmorphic",
    "rainbow",
    "gaming aesthetic",
    "gamer aesthetic",
    "cartoonish",
    "cartoon style",
    "cartoon-style",
    "anime style",
    "childish",
    "psychedelic",
    "vaporwave",
    "grunge",
    "meme",
)

# Colour words only count as violations when they qualify something brand-controlled.
_BRAND_SURFACE_WORDS = (
    "brand",
    "logo",
    "accent",
    "palette",
    "overlay",
    "caption",
    "end card",
    "endcard",
    "title card",
    "lower third",
    "background gradient",
    "colour scheme",
    "color scheme",
    "theme",
    "ui",
    "button",
    "chart",
)

_GRADIENT_TERMS = ("gradient", "gradients")


def _contains_phrase(haystack: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", haystack) is not None


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"[.!?\n;]+", text) if s.strip()]


class BrandAlignmentChecker:
    """Applies the brand rules. Stateless and safe to share."""

    # ------------------------------------------------------------------ pre-generation
    def check_text(
        self,
        text: str,
        projection: BrandProjection,
        *,
        component: str = "Brief",
    ) -> list[BrandFinding]:
        """Advisory scan of a brief / spec / scene before anything is generated."""
        if projection.is_empty or not text:
            return []
        lowered = text.lower()
        findings: list[BrandFinding] = []

        for term in _STYLE_TERMS:
            if _contains_phrase(lowered, term):
                findings.append(
                    BrandFinding(
                        code="brand_style_conflict",
                        severity=Severity.MINOR,
                        message=(
                            f"The request asks for a '{term}' look, which the brand design "
                            f"system rules out."
                        ),
                        affected_component=component,
                        recommendation=(
                            "Reword towards the brand's clean, professional, trustworthy "
                            "look, or accept that this generation will be off-brand."
                        ),
                        details={"term": term},
                    )
                )

        # A forbidden colour only matters where it would be a *brand* colour, so it must
        # co-occur with a brand-surface word in the same sentence.
        for sentence in _sentences(lowered):
            if not any(_contains_phrase(sentence, w) for w in _BRAND_SURFACE_WORDS):
                continue
            for colour in projection.forbidden_colours:
                needle = colour.lower().replace(" colors", "").replace(" colours", "").strip()
                if needle and _contains_phrase(sentence, needle):
                    findings.append(
                        BrandFinding(
                            code="brand_colour_conflict",
                            severity=Severity.MINOR,
                            message=(
                                f"The request uses {colour} as a brand or accent colour, which "
                                f"is outside the brand palette."
                            ),
                            affected_component=component,
                            recommendation=(
                                "Brand-controlled colour should stay within "
                                + ", ".join(projection.palette_hexes)
                                + ". Natural colour in the scene is unaffected."
                            ),
                            details={"colour": colour, "sentence": sentence[:200]},
                        )
                    )
                    break  # one colour finding per sentence is enough

        if any(_contains_phrase(lowered, term) for term in _GRADIENT_TERMS):
            findings.append(
                BrandFinding(
                    code="brand_gradient_discouraged",
                    severity=Severity.INFO,
                    message="Gradients are not part of the brand's default visual language.",
                    affected_component=component,
                    recommendation=(
                        "Prefer flat brand colour. If a gradient is unavoidable, keep it "
                        "within one brand hue."
                    ),
                )
            )
        return _dedupe_findings(findings)

    # ------------------------------------------------------------------- deterministic
    def check_brand_colour(
        self,
        hex_value: str,
        projection: BrandProjection,
        *,
        component: str,
        guidelines_compatible: bool | None,
    ) -> BrandFinding | None:
        """Test one brand-controlled colour we chose ourselves.

        `guidelines_compatible` is the verdict from `BrandGuidelines.is_brand_compatible`
        (None when there is no palette to judge against), passed in so this stays pure.
        """
        if guidelines_compatible is None or guidelines_compatible:
            return None
        return BrandFinding(
            code="brand_colour_off_palette",
            severity=Severity.MINOR,
            message=(
                f"{component} uses {hex_value.upper()}, which is not the brand palette or a "
                f"shade derived from it."
            ),
            affected_component=component,
            recommendation=(
                "Use "
                + (", ".join(projection.palette_hexes) or "a brand palette colour")
                + " or a shade derived from one of them."
            ),
            details={"colour": hex_value.upper(), "palette": list(projection.palette_hexes)},
        )

    def check_composition_colours(
        self,
        colours: Sequence,
        projection: BrandProjection,
    ) -> list[BrandFinding]:
        """Check every brand-controlled colour used by the final composition.

        `colours` is a sequence of `(component, hex_value, compatible)` triples.
        """
        findings: list[BrandFinding] = []
        for component, hex_value, compatible in colours:
            finding = self.check_brand_colour(
                hex_value,
                projection,
                component=component,
                guidelines_compatible=compatible,
            )
            if finding is not None:
                findings.append(finding)
        return findings


def _dedupe_findings(findings: Iterable[BrandFinding]) -> list[BrandFinding]:
    seen = set()
    out: list[BrandFinding] = []
    for finding in findings:
        key = (finding.code, finding.affected_component, (finding.details or {}).get("term"))
        if key in seen:
            continue
        seen.add(key)
        out.append(finding)
    return out
