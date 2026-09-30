# Ported from phi-service-layer/media-generation/backend/app/brand (2026-09-29).
"""Brand guidelines — the structured form of `DESIGN.md`.

`DESIGN.md` is the project's brand source of truth. This module holds its *parsed*
shape as pure, immutable data: no I/O, no parsing, no HTTP. `design_document.py`
produces one of these; `projections.py` slices it per generation stage.

**An important interpretation decision is encoded here.** `DESIGN.md` is written as a
UI/visual design system for digital products, and read literally its colour rules say
"do not use blue / green / purple". Applied naively to generated *media* that would be
absurd — a family in a park has a blue sky and green grass, and refusing those would
produce nonsense while claiming to be on-brand.

So the rules are split by what they actually govern:

* `palette` / `forbidden_colours` govern **brand-controlled colour**: accent colour,
  graphics, overlays, captions, end cards, any on-screen UI, and the logo lockup.
  They do **not** govern natural scene colour (sky, foliage, skin, clothing, product
  materials).
* `avoid_styles`, `appearance` and `identity_values` govern the **aesthetic** of the
  whole frame, and those do apply everywhere — no cartoon, neon, cyberpunk or
  gaming look; clean, professional, trustworthy, generous whitespace.
* `logo_rules` govern the logo wherever it is composited.

`source_digest` is the SHA-256 of the `DESIGN.md` that produced this object. It is
recorded on every generation so it is always answerable *which* brand document a piece
of media was generated against.
"""

from __future__ import annotations

import colorsys
import re
from dataclasses import dataclass, field

_HEX_RE = re.compile(r"^#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$")

# A colour this desaturated reads as a neutral (white / grey / black). DESIGN.md's
# palette is red + grey + white and it explicitly permits *derived* shades, so any
# near-achromatic colour is brand-compatible as a neutral.
NEUTRAL_SATURATION_MAX = 0.12
# How far a saturated colour's hue may sit from a palette hue and still count as a
# derived shade of it rather than an unrelated hue.
BRAND_HUE_TOLERANCE_DEGREES = 20.0


@dataclass(frozen=True)
class ColourToken:
    """A named brand colour, e.g. ``ColourToken("Prudential Red", "#E71D2D")``."""

    name: str
    hex_value: str
    usage: str | None = None

    def __post_init__(self) -> None:
        if not _HEX_RE.match(self.hex_value):
            raise ValueError(f"not a hex colour: {self.hex_value!r}")

    @property
    def normalised(self) -> str:
        """Upper-case 6-digit form, so ``#fff`` and ``#FFFFFF`` compare equal."""
        value = self.hex_value.lstrip("#")
        if len(value) == 3:
            value = "".join(ch * 2 for ch in value)
        return "#" + value.upper()

    @property
    def rgb(self) -> tuple[int, int, int]:
        v = self.normalised.lstrip("#")
        return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))

    @property
    def hsl(self) -> tuple[float, float, float]:
        """(hue degrees, saturation 0-1, lightness 0-1)."""
        r, g, b = (channel / 255.0 for channel in self.rgb)
        hue, lightness, saturation = colorsys.rgb_to_hls(r, g, b)
        return hue * 360.0, saturation, lightness

    def describe(self) -> str:
        return f"{self.name} {self.normalised}" + (f" ({self.usage})" if self.usage else "")


@dataclass(frozen=True)
class BrandGuidelines:
    """The parsed brand system. Every collection is a tuple so this stays hashable."""

    source_path: str | None = None
    source_digest: str | None = None
    palette: tuple[ColourToken, ...] = field(default_factory=tuple)
    forbidden_colours: tuple[str, ...] = field(default_factory=tuple)
    identity_values: tuple[str, ...] = field(default_factory=tuple)
    appearance: tuple[str, ...] = field(default_factory=tuple)
    avoid_styles: tuple[str, ...] = field(default_factory=tuple)
    typography: tuple[str, ...] = field(default_factory=tuple)
    logo_rules: tuple[str, ...] = field(default_factory=tuple)
    photography_rules: tuple[str, ...] = field(default_factory=tuple)
    illustration_rules: tuple[str, ...] = field(default_factory=tuple)
    design_principles: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def unavailable(cls, *, source_path: str | None = None) -> BrandGuidelines:
        """No `DESIGN.md` was found. Callers must degrade, not invent a brand."""
        return cls(source_path=source_path, source_digest=None)

    @property
    def available(self) -> bool:
        """True when a design document was actually parsed with usable content."""
        return self.source_digest is not None and bool(self.palette or self.identity_values)

    @property
    def palette_hexes(self) -> tuple[str, ...]:
        return tuple(token.normalised for token in self.palette)

    @property
    def primary(self) -> ColourToken | None:
        """The brand's primary accent — the first palette entry that is not white/black."""
        for token in self.palette:
            if token.normalised not in ("#FFFFFF", "#000000"):
                return token
        return self.palette[0] if self.palette else None

    @property
    def neutral(self) -> ColourToken | None:
        """The secondary/neutral colour — the darkest non-primary, non-white entry."""
        primary = self.primary
        candidates = [
            t
            for t in self.palette
            if t.normalised != "#FFFFFF" and (primary is None or t.normalised != primary.normalised)
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda t: sum(t.rgb))

    def is_in_palette(self, hex_value: str) -> bool:
        """Exact palette membership. Derived shades are checked by `nearest_distance`."""
        try:
            token = ColourToken("probe", hex_value)
        except ValueError:
            return False
        return token.normalised in self.palette_hexes

    def is_brand_compatible(self, hex_value: str) -> bool | None:
        """Whether a brand-controlled colour is on-brand, allowing derived shades.

        `DESIGN.md` permits derived shades of the brand colours but forbids unrelated
        hues, so straight palette membership is too strict and RGB distance is the wrong
        metric — near-black is numerically far from every palette entry yet is a perfectly
        good derived neutral, while a saturated blue can sit closer. The test is therefore
        on hue:

        * near-achromatic (white / grey / black) -> compatible, it is a neutral;
        * otherwise its hue must be within `BRAND_HUE_TOLERANCE_DEGREES` of a palette hue.

        Returns None when there is no palette to judge against.
        """
        if not self.palette:
            return None
        try:
            probe = ColourToken("probe", hex_value)
        except ValueError:
            return False

        hue, saturation, _ = probe.hsl
        if saturation <= NEUTRAL_SATURATION_MAX:
            return True

        for token in self.palette:
            token_hue, token_saturation, _ = token.hsl
            if token_saturation <= NEUTRAL_SATURATION_MAX:
                continue  # a neutral palette entry has no meaningful hue to match
            delta = abs(hue - token_hue) % 360.0
            if min(delta, 360.0 - delta) <= BRAND_HUE_TOLERANCE_DEGREES:
                return True
        return False

    def nearest_distance(self, hex_value: str) -> float | None:
        """Euclidean RGB distance to the closest palette colour, or None if no palette.

        `DESIGN.md` permits *derived* shades of the brand colours, so exact membership is
        too strict a test for a real overlay colour. Distance lets a caller accept a
        derived shade while still rejecting an unrelated hue.
        """
        if not self.palette:
            return None
        try:
            probe = ColourToken("probe", hex_value).rgb
        except ValueError:
            return None
        return min(
            ((probe[0] - t.rgb[0]) ** 2 + (probe[1] - t.rgb[1]) ** 2 + (probe[2] - t.rgb[2]) ** 2)
            ** 0.5
            for t in self.palette
        )
