# Changelog

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
