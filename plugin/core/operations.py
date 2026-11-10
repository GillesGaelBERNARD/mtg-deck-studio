"""The operation registry metadata. operations.json is the only list of operations."""
import json
import re
from functools import lru_cache
from pathlib import Path

from .errors import StudioError

_PATH = Path(__file__).resolve().parent.parent / "operations.json"
_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}


@lru_cache(maxsize=1)
def _registry_cached(mtime: float) -> dict:
    data = json.loads(_PATH.read_text(encoding="utf-8"))
    return {op["name"]: op for op in data["operations"]}


def registry() -> dict:
    return _registry_cached(_PATH.stat().st_mtime)


def public_names() -> list:
    return sorted(name for name, op in registry().items() if not op.get("internal"))


def get(name: str) -> dict:
    op = registry().get(name)
    if op is None or op.get("internal"):
        raise StudioError("invalid", f"Unknown operation '{name}'.", known=public_names())
    return op


def _is_type(value, name: str) -> bool:
    if name == "null":
        return value is None
    if isinstance(value, bool) and name in ("integer", "number"):
        return False
    return isinstance(value, _TYPES[name])


def _check(value, schema: dict, path: str):
    expected = schema.get("type")
    if expected:
        allowed = expected if isinstance(expected, list) else [expected]
        if not any(_is_type(value, name) for name in allowed):
            raise StudioError("invalid", f"{path} must be {' or '.join(allowed)}.")
    if "enum" in schema and value not in schema["enum"]:
        raise StudioError("invalid", f"{path} must be one of {schema['enum']}.")
    if isinstance(value, str) and "pattern" in schema and not re.fullmatch(schema["pattern"], value):
        raise StudioError("invalid", f"{path} has an invalid format.")
    if isinstance(value, str) and "maxLength" in schema and len(value) > schema["maxLength"]:
        raise StudioError("invalid", f"{path} is longer than {schema['maxLength']} characters.")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise StudioError("invalid", f"{path} must be at least {schema['minimum']}.")
        if "maximum" in schema and value > schema["maximum"]:
            raise StudioError("invalid", f"{path} must be at most {schema['maximum']}.")
    if isinstance(value, list):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise StudioError("invalid", f"{path} has more than {schema['maxItems']} items.")
        for i, item in enumerate(value):
            _check(item, schema.get("items", {}), f"{path}[{i}]")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                raise StudioError("invalid", f"{path}.{key} is required.")
        for key, item in value.items():
            if key not in props:
                if schema.get("additionalProperties", True) is False:
                    raise StudioError("invalid", f"{path}.{key} is not a parameter of this operation.")
                continue
            _check(item, props[key], f"{path}.{key}")


def validate(name: str, args: dict) -> dict:
    op = get(name)
    _check(args, {**op["params"], "additionalProperties": False}, "args")
    return op
