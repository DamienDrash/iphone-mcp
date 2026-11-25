# Contributing

Thanks for helping! Bug reports, device/iOS compatibility notes and pull requests are all welcome.
Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).
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
  `~/Library/Logs/iphone-mcp/wda-<udid>.log` (remove anything personal first).

## Where to start

- Issues labelled [`good first issue`](https://github.com/DamienDrash/iphone-mcp/labels/good%20first%20issue)
  are small, self-contained and do not need an iPhone unless they say so.
- **Compatibility reports are just as valuable as code.** iphone-mcp is only tested on the maintainer's own
  iPhone. If you run it on Windows, on iOS 17/18 or over Wi-Fi without a Mac, run `iphone-mcp selftest` and
  share the result with the *Compatibility report* issue form.

## Pull request flow

1. For anything bigger than a small fix, open an issue first so we can agree on the approach.
2. Fork the repository and create a branch from `main`.
3. Make the change with tests. Unit tests run against a simulated WebDriverAgent (`tests/test_tools.py`),
   so most changes can be tested without a device.
4. Run all checks locally (see *Development setup*) and add an entry under *Unreleased* in `CHANGELOG.md`
   for anything users will notice.
5. Open the pull request and fill in the template, including whether you tested on a real iPhone.
   CI runs the same checks on macOS, Linux and Windows with Python 3.11–3.13.
6. The maintainer reviews, may ask for changes, and squash-merges. Releases are cut separately.

## Security

Never open a public issue for a vulnerability. Use the
[private reporting form](https://github.com/DamienDrash/iphone-mcp/security/advisories/new) – see [SECURITY.md](SECURITY.md).

