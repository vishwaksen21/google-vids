"""Pydantic JSON Schema -> the OpenAPI-subset schema Gemini's `response_schema` accepts.

Gemini's `response_schema` (as passed through the company gateway to Google's config
object) takes a subset of OpenAPI 3: no `$ref`, no `$defs`, no `additionalProperties`,
no `title`/`default`, and nullability as `nullable: true` instead of `anyOf [.., null]`.
This inlines references and rewrites those constructs; anything it cannot express is
dropped rather than sent, and the response is always validated against the full Pydantic
model afterwards anyway.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

_DROP = {"title", "default", "additionalProperties", "examples", "$schema", "discriminator",
         "const", "exclusiveMinimum", "exclusiveMaximum"}
_KEEP_FORMATS = {"date-time", "enum"}


def to_gemini_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})
    return _convert(schema, defs, depth=0)


def _convert(node: Any, defs: dict[str, Any], depth: int) -> Any:
    if depth > 20:
        return {"type": "string"}
    if isinstance(node, list):
        return [_convert(item, defs, depth + 1) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        merged = {**defs.get(name, {}), **{k: v for k, v in node.items() if k != "$ref"}}
        return _convert(merged, defs, depth + 1)
    if "anyOf" in node:
        options = [opt for opt in node["anyOf"] if opt.get("type") != "null"]
        nullable = len(options) != len(node["anyOf"])
        base = _convert(options[0], defs, depth + 1) if len(options) == 1 else {"type": "string"}
        if nullable:
            base = {**base, "nullable": True}
        if "description" in node:
            base["description"] = node["description"]
        return base
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _DROP:
            continue
        if key == "format" and value not in _KEEP_FORMATS:
            continue
        if key == "properties":
            out[key] = {name: _convert(sub, defs, depth + 1) for name, sub in value.items()}
        elif key in ("items",):
            out[key] = _convert(value, defs, depth + 1)
        elif key == "enum":
            out[key] = [str(v) for v in value]
            out.setdefault("type", "string")
        else:
            out[key] = value
    if out.get("type") == "integer" and "enum" in out:
        out["type"] = "string"
    return out
