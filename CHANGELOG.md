# Changelog

## Unreleased

Additions from the Glama tool review, taken into 1.0 on purpose (the only interface change after the 0.9 freeze).
Every new parameter is optional, so existing calls work unchanged.

- `iphone_type` can **replace or clear** the focused field (`replace=true`; with empty text it only clears).
- `iphone_swipe` opens **notifications or Control Center** (`system=…`) without coordinates. Tested on a real
  iPhone 15 Pro Max (iOS 26), like replacing text and rotating.
- `iphone_pinch` can **rotate** with two fingers (`rotation` in degrees); `scale` now defaults to 1.
- Tool descriptions say where device settings are changed (Settings app, Control Center) and how to close a
  system panel.

## 0.9.0 – 2026-11-19 · release candidate for 1.0

The tool interface is now frozen: until 1.0 only fixes and documentation change.

- Starting WebDriverAgent **without Xcode over USB** (pymobiledevice3, root-free tunnel) verified on a real
  iPhone: `iphone-mcp selftest` 18/18.
- Fixed: searching for text could fail with "stale element reference" while the screen was changing; the search
  now retries.
- `iphone-mcp selftest` stops with a clear message when the iPhone locks itself during the run.

## 0.3.0 – 2026-11-19

- **Self-healing:** if WebDriverAgent crashed or the connection dropped, the next tool call restarts it and retries
  once (`IPHONE_MCP_AUTO_RECOVER`, on by default).
- **Cleanup:** WebDriverAgent is stopped when the last iphone-mcp server exits (`IPHONE_MCP_STOP_ON_EXIT`, on by
  default); one started with `iphone-mcp start` keeps running.
- **New command `iphone-mcp selftest`:** drives the real iPhone through a harmless, language-independent sequence.
- Interface snapshot test, type checking with mypy in CI, and tests against a simulated WebDriverAgent.
- Dependencies are capped at the major versions iphone-mcp is tested with; `pydantic` is declared explicitly.
- Fixed: on Windows, checking whether a process runs could terminate it.
- README: *Reliability*, *Versioning* and *Tested on* sections; Linux (Ubuntu VM) verified driving the iPhone over Wi-Fi.

## 0.2.0 – 2026-11-19

- **New tools:** `iphone_wait_for` (wait until a text appears or disappears) and `iphone_pinch` (two-finger zoom).
- **Changed:** `iphone_double_tap` and `iphone_long_press` are now part of `iphone_tap` (`gesture="double"` / `"long"`).
- Every tool has a detailed description (purpose, when to use it, behaviour), described and range-checked
  parameters, and MCP tool annotations (read-only, destructive, idempotent).
- Several agent windows can share one iPhone: iphone-mcp joins the running WebDriverAgent session instead of
  replacing it, and no longer stops an `xcodebuild` that another iphone-mcp process started.
- Tapping or waiting by text no longer fails when an element disappears while it is being read.
- Dependabot and CodeQL code scanning for the repository.

## 0.1.2 – 2026-11-19

- GitHub Sponsors: Sponsor button on the repository, funding link on PyPI, a support section in the README
  and a short note after `iphone-mcp setup`.

## 0.1.1 – 2026-11-19

- Package metadata lists the supported Python versions (3.11–3.13); CI now tests all three.

## 0.1.0 – 2026-11-19

First public release.

- 18 MCP tools: screenshots scaled to tap coordinates, UI tree, tap / double-tap / long-press / tap-by-text,
  swipe / scroll, typing, hardware buttons, lock / unlock, app list / launch / close, URLs and deep links.
- Connection over USB or Wi-Fi, with automatic fallback: Xcode tunnel → usbmux → last known Wi-Fi IP.
- Keep-awake: the screen does not auto-lock while an agent is connected (invisible off-screen touch, no settings changed).
- `iphone-mcp setup` builds and signs WebDriverAgent with your own Apple ID (macOS + Xcode).
- Linux and Windows: launch WebDriverAgent without Xcode through pymobiledevice3's no-root tunnel;
  `setup --prebuilt` / `setup --ipa` for installing a signed WebDriverAgent.
- `iphone-mcp doctor` checks the whole chain.
