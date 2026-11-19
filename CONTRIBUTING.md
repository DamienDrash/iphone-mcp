# Contributing

Thanks for helping! Bug reports, device/iOS compatibility notes and pull requests are all welcome.
You can also support the project financially through [GitHub Sponsors](https://github.com/sponsors/DamienDrash).

## Development setup

```bash
git clone https://github.com/DamienDrash/iphone-mcp.git
cd iphone-mcp
uv sync
uv run pytest
uv run mypy
uv run ruff check . && uv run ruff format --check .
```

Try your changes against a real device with `uv run iphone-mcp doctor` and `uv run iphone-mcp start`,
or drive the server from any MCP client with `uv --directory /path/to/iphone-mcp run iphone-mcp`.

## Guidelines

- The tool interface is pinned in `tests/api_contract.json`. If you change a tool on purpose, regenerate it with
  `uv run python -m tests.contract > tests/api_contract.json` and describe the change in `CHANGELOG.md`.
- Run `uv run iphone-mcp selftest` against a real iPhone before sending changes to tools or the connection.

- Keep tools small and predictable; return short, factual text the agent can act on.
- Never add anything that types passwords, passcodes or payment data on the user's behalf.
- Device-specific behaviour (iOS version, locale) belongs behind identifiers, not on-screen text.
- When you report a bug, include the output of `uv run iphone-mcp doctor` and the end of
  `~/Library/Logs/iphone-mcp/wda.log` (remove anything personal first).
