# Contributing

Thanks for helping! Bug reports, device/iOS compatibility notes and pull requests are all welcome.

## Development setup

```bash
git clone https://github.com/DamienDrash/iphone-mcp.git
cd iphone-mcp
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Try your changes against a real device with `uv run iphone-mcp doctor` and `uv run iphone-mcp start`,
or drive the server from any MCP client with `uv --directory /path/to/iphone-mcp run iphone-mcp`.

## Guidelines

- Keep tools small and predictable; return short, factual text the agent can act on.
- Never add anything that types passwords, passcodes or payment data on the user's behalf.
- Device-specific behaviour (iOS version, locale) belongs behind identifiers, not on-screen text.
- When you report a bug, include the output of `uv run iphone-mcp doctor` and the end of
  `~/Library/Logs/iphone-mcp/wda.log` (remove anything personal first).
