# Ported from phi-service-layer/media-generation/backend/app/brand (2026-09-29).
"""Brand context — `DESIGN.md` as a first-class input to every generation stage.

    guidelines.py       the parsed brand system (pure data)
    design_document.py  DESIGN.md -> BrandGuidelines
    projections.py      per-stage slices injected into prompts
    alignment.py        brand rules -> findings
    style.py            composition colours derived from the palette
"""

from vidsprod.brand.alignment import BrandAlignmentChecker, BrandFinding
from vidsprod.brand.design_document import load_brand_guidelines, parse_design_document
from vidsprod.brand.guidelines import BrandGuidelines, ColourToken
from vidsprod.brand.projections import BrandProjection, BrandStage, project
from vidsprod.brand.style import BrandStyle

__all__ = [
    "BrandAlignmentChecker",
    "BrandFinding",
    "BrandGuidelines",
    "BrandProjection",
    "BrandStage",
    "BrandStyle",
    "ColourToken",
    "load_brand_guidelines",
    "parse_design_document",
    "project",
]
