"""Guards the public interface: renaming a tool, removing or retyping a parameter, or making an optional
parameter required breaks existing agents and prompts. If this test fails on purpose, update the snapshot
(see tests/contract.py), add a CHANGELOG entry and bump the version as described in README "Versioning"."""

import json
from pathlib import Path

from tests.contract import tool_interface

SNAPSHOT = json.loads((Path(__file__).parent / "api_contract.json").read_text())


def test_tool_interface_matches_snapshot():
    current = tool_interface()
    assert sorted(current) == sorted(SNAPSHOT), "tools were added or removed"
    for name, spec in SNAPSHOT.items():
        assert current[name] == spec, f"interface of {name} changed"
