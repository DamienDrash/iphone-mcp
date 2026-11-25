## What and why

<!-- What does this change, and which issue does it address (e.g. "Fixes #12")? -->

## Checks

- [ ] `uv run pytest`, `uv run mypy`, `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] If a tool changed on purpose: `tests/api_contract.json` regenerated and the change described in `CHANGELOG.md`
- [ ] `CHANGELOG.md` has an entry under *Unreleased* for user-visible changes
- [ ] Nothing types passwords, passcodes or payment data, and no credentials are in the diff

## Tested on a real iPhone?

<!-- e.g. "selftest 18/18 on iPhone 13, iOS 18.2, Windows 11" – or "No, unit tests only" (that is fine for docs and small fixes). -->
