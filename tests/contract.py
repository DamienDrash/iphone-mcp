"""The public tool interface, as MCP clients see it. Used by test_api_contract.py.

Run `uv run python -m tests.contract > tests/api_contract.json` to update the snapshot – only on purpose,
together with a CHANGELOG entry and the right version bump (see README "Versioning").
"""

import asyncio
import json

from iphone_mcp.server import mcp


def tool_interface() -> dict:
    tools = asyncio.run(mcp.list_tools())
    out = {}
    for t in sorted(tools, key=lambda t: t.name):
        schema = t.input_schema or {}
        props = schema.get("properties", {})
        out[t.name] = {
            "params": {
                name: {
                    "type": p.get("type") or [a.get("type") for a in p.get("anyOf", [])],
                    "enum": p.get("enum"),
                    "required": name in schema.get("required", []),
                }
                for name, p in sorted(props.items())
            },
            "read_only": t.annotations.read_only_hint if t.annotations else None,
            "destructive": t.annotations.destructive_hint if t.annotations else None,
        }
    return out


if __name__ == "__main__":
    print(json.dumps(tool_interface(), indent=2, ensure_ascii=False))
