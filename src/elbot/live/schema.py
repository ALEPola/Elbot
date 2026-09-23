"""Fail-closed validation of model-supplied tool arguments."""


def validate_arguments(name, args):
    from .session import DEFAULT_TOOLS

    schema = next((t["parameters"] for t in DEFAULT_TOOLS if t.get("name") == name), None)
    if schema is None or not isinstance(args, dict):
        raise ValueError("Unknown tool or invalid arguments object")
    properties = schema["properties"]
    if set(args) != set(schema["required"]):
        raise ValueError("Arguments must match the approved tool schema")
    for key, value in args.items():
        kinds = properties[key]["type"]
        kinds = [kinds] if isinstance(kinds, str) else kinds
        kind = {str: "string", int: "integer", bool: "boolean", type(None): "null"}.get(type(value))
        if kind not in kinds:
            raise ValueError("Invalid argument type")
        if kind == "string" and (not value.strip() or len(value) > (200 if key == "note" else 500)):
            raise ValueError("Invalid text argument length")
        if key == "level" and not 0 <= value <= 200:
            raise ValueError("Volume must be 0–200")
        if key in ("position", "limit") and value is not None and not 1 <= value <= 1000:
            raise ValueError("Invalid queue position or limit")
