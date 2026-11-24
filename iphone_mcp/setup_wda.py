"""`iphone-mcp setup`: download, sign and build WebDriverAgent for your iPhone (macOS + Xcode)."""

from __future__ import annotations

import asyncio
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from . import paths
from .connection import coredevice_devices

WDA_REPO = "https://github.com/appium/WebDriverAgent.git"
WDA_REF = os.environ.get("IPHONE_MCP_WDA_REF", "v16.15.3")  # pinned, tested release


SPONSOR_LINE = "\n💖 iphone-mcp is free. If it helps you, consider sponsoring: https://github.com/sponsors/DamienDrash"


class SetupError(RuntimeError):
    pass


def several_iphones_hint(udid: str) -> str:
    """After setup: how to run one server per iPhone once more than one iPhone is set up."""
    devices = paths.configured_devices()
    if len(devices) < 2:
        return ""
    listing = "\n".join(
        f"  {d.get('name') or 'iPhone'}: {u}{'  (default, just set up)' if u == udid else ''}"
        for u, d in devices.items()
    )
    return (
        f"\niPhones set up so far:\n{listing}\n"
        "A server without IPHONE_MCP_UDID controls the default one. Give each iPhone its own server, e.g.:\n"
        "  claude mcp add iphone-a -e IPHONE_MCP_UDID=<udid-a> -- uvx iphone-mcp"
    )


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def find_teams() -> list[str]:
    """Team IDs (certificate OU) of all 'Apple Development' signing certificates in the keychain."""
    out = _run(["security", "find-certificate", "-a", "-c", "Apple Development", "-p"]).stdout
    teams: list[str] = []
    for pem in re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", out, re.S):
        subject = _run(["openssl", "x509", "-noout", "-subject"], input=pem).stdout
        m = re.search(r"OU\s*=\s*([A-Z0-9]{10})", subject)
        if m and m.group(1) not in teams:
            teams.append(m.group(1))
    return teams


def pick_device(udid: str | None) -> dict:
    devices = asyncio.run(coredevice_devices())
    if udid:
        devices = [d for d in devices if d["udid"] == udid]
    reachable = [d for d in devices if d.get("tunnelState") != "unavailable"]
    if not reachable:
        known = "\n".join(f"  {d['name']} ({d['udid']}): {d.get('tunnelState')}" for d in devices) or "  none"
        raise SetupError(
            f"No reachable iPhone. Connect it with a cable, unlock it and tap 'Trust'.\nDevices Xcode knows:\n{known}"
        )
    if len(reachable) > 1:
        listing = "\n".join(f"  {d['name']}: {d['udid']}" for d in reachable)
        raise SetupError(f"Several iPhones found, pick one with --udid:\n{listing}")
    return reachable[0]


def ensure_source() -> None:
    if (paths.WDA_SRC / "WebDriverAgent.xcodeproj").exists():
        current = _run(["git", "-C", str(paths.WDA_SRC), "describe", "--tags", "--exact-match"]).stdout.strip()
        if current == WDA_REF:
            return
        shutil.rmtree(paths.WDA_SRC)
    print(f"→ Downloading WebDriverAgent {WDA_REF} …")
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    r = _run(["git", "clone", "--depth", "1", "--branch", WDA_REF, WDA_REPO, str(paths.WDA_SRC)])
    if r.returncode:
        raise SetupError(f"git clone failed:\n{r.stderr}")


def build(udid: str, team: str, bundle_id: str) -> None:
    paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        "xcodebuild",
        "build-for-testing",
        "-project",
        str(paths.WDA_SRC / "WebDriverAgent.xcodeproj"),
        "-scheme",
        "WebDriverAgentRunner",
        "-destination",
        f"id={udid}",
        "-derivedDataPath",
        str(paths.WDA_DERIVED),
        "-allowProvisioningUpdates",
        f"DEVELOPMENT_TEAM={team}",
        "CODE_SIGN_STYLE=Automatic",
        f"PRODUCT_BUNDLE_IDENTIFIER={bundle_id}",
    ]
    print("→ Building WebDriverAgent (first time: 2–5 minutes) …")
    with open(paths.BUILD_LOG, "w") as log:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        assert proc.stdout
        for line in proc.stdout:
            log.write(line)
            if " error:" in line or line.startswith("** ") or "requires a provisioning profile" in line:
                print("   " + line.rstrip())
        proc.wait()
    if proc.returncode or not list(paths.WDA_DERIVED.glob("Build/Products/*.xctestrun")):
        tail = "".join(paths.BUILD_LOG.read_text().splitlines(keepends=True)[-25:])
        raise SetupError(f"Build failed. Full log: {paths.BUILD_LOG}\n{tail}")


# ---------------------------------------------------------------- without Xcode (Linux / Windows)
PREBUILT_URL = "https://github.com/appium/WebDriverAgent/releases/download/{ref}/WebDriverAgentRunner-Runner.zip"
UNSIGNED_IPA = paths.DATA_DIR / "WebDriverAgentRunner-unsigned.ipa"

SIGNING_HELP = """\
Next: sign this file with YOUR Apple ID, then install it with iphone-mcp.

  Windows / macOS (free Apple ID):  Sideloadly – https://sideloadly.io
      open the .ipa, sign in with your Apple ID, untick "install" if offered and
      export the signed .ipa (or let Sideloadly install it directly).
  Linux (paid developer account):   zsign – https://github.com/zhlynn/zsign
      zsign -k cert.p12 -p PASSWORD -m profile.mobileprovision -o signed.ipa {ipa}
      (create the certificate and a wildcard development profile at developer.apple.com)

Then:  iphone-mcp setup --ipa signed.ipa
(Skip that step if your signing tool already installed the app; run `iphone-mcp setup --ipa` with
 the same file anyway so iphone-mcp learns the app's bundle ID, or set IPHONE_MCP_RUNNER_BUNDLE_ID.)"""


def download_prebuilt() -> Path:
    """Appium's unsigned real-device build of WebDriverAgent, repackaged as an .ipa."""
    url = PREBUILT_URL.format(ref=WDA_REF)
    print(f"→ Downloading prebuilt WebDriverAgent {WDA_REF} …")
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        zpath = Path(tmp) / "runner.zip"
        try:
            urllib.request.urlretrieve(url, zpath)
        except OSError as e:
            raise SetupError(f"Download failed ({url}): {e}") from None
        # The zip holds "WebDriverAgentRunner-Runner.app/…"; an .ipa needs "Payload/<name>.app/…"
        with zipfile.ZipFile(zpath) as src, zipfile.ZipFile(UNSIGNED_IPA, "w", zipfile.ZIP_DEFLATED) as ipa:
            for item in src.infolist():
                if item.is_dir() or ".dSYM/" in item.filename:
                    continue
                target = zipfile.ZipInfo("Payload/" + item.filename.removeprefix("Payload/"), item.date_time)
                target.external_attr = item.external_attr  # keep the executable bit
                ipa.writestr(target, src.read(item), zipfile.ZIP_DEFLATED)
    return UNSIGNED_IPA


def ipa_bundle_id(ipa: Path) -> str:
    with zipfile.ZipFile(ipa) as z:
        plists = [n for n in z.namelist() if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", n)]
        if not plists:
            raise SetupError(f"{ipa} does not look like an iOS app (.ipa with Payload/*.app)")
        return plistlib.loads(z.read(plists[0]))["CFBundleIdentifier"]


def install_ipa(ipa: Path, udid: str | None) -> tuple[str, str]:
    """Install a signed WebDriverAgent .ipa over USB (any OS). Returns (udid, runner bundle id)."""
    import asyncio

    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.services.installation_proxy import InstallationProxyService

    bundle = ipa_bundle_id(ipa)

    async def go() -> str:
        lockdown = await create_using_usbmux(udid)
        print(f"→ Installing {bundle} on {lockdown.display_name} (iOS {lockdown.product_version}) …")
        await InstallationProxyService(lockdown).install_from_local(ipa)
        return str(lockdown.udid)

    try:
        return asyncio.run(go()), bundle
    except Exception as e:  # noqa: BLE001
        raise SetupError(
            f"Installing failed: {e!r}\nIs the iPhone connected by cable, unlocked and trusting this computer? "
            "Is the .ipa signed for this iPhone?"
        ) from None


def run_without_xcode(udid: str | None, ipa: str | None) -> int:
    try:
        if not ipa:
            out = download_prebuilt()
            print(f"✓ Unsigned WebDriverAgent saved to {out}\n")
            print(SIGNING_HELP.format(ipa=out))
            return 0
        device, bundle = install_ipa(Path(ipa).expanduser(), udid or os.environ.get("IPHONE_MCP_UDID"))
        paths.save_device_setup(device, runner_bundle_id=bundle, launcher="pymobiledevice3", wda_ref=WDA_REF)
    except SetupError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    print(f"✓ Installed. iphone-mcp will start {bundle} without Xcode.")
    print("  On the iPhone: Settings → General → VPN & Device Management → trust your developer certificate,")
    print("  Settings → Privacy & Security → Developer Mode: on, Settings → Developer → Enable UI Automation: on.")
    print("  Check with: iphone-mcp doctor   ·   try it with: iphone-mcp start")
    if hint := several_iphones_hint(device):
        print(hint)
    print(SPONSOR_LINE)
    return 0


# ---------------------------------------------------------------- with Xcode (macOS)
def run(
    udid: str | None = None,
    team: str | None = None,
    bundle_id: str | None = None,
    prebuilt: bool = False,
    ipa: str | None = None,
) -> int:
    if ipa or prebuilt or not paths.IS_MAC:
        return run_without_xcode(udid, ipa)
    try:
        if not shutil.which("xcodebuild"):
            raise SetupError(
                "Xcode not found. Install it from the App Store, then run: sudo xcode-select -s /Applications/Xcode.app"
            )

        dev = pick_device(udid or os.environ.get("IPHONE_MCP_UDID"))
        if dev.get("developerMode") not in (None, "enabled"):
            print("⚠ Developer Mode looks disabled: Settings → Privacy & Security → Developer Mode")

        team = team or os.environ.get("IPHONE_MCP_TEAM_ID")
        if not team:
            teams = find_teams()
            if not teams:
                raise SetupError(
                    "No 'Apple Development' certificate found. Open Xcode → Settings → Accounts, sign in with "
                    "your Apple ID (a free one works) and click 'Manage Certificates' → '+' → Apple Development."
                )
            if len(teams) > 1:
                raise SetupError(f"Several teams found, pick one with --team: {', '.join(teams)}")
            team = teams[0]

        previous = paths.wda_config(dev["udid"])
        bundle_id = (
            bundle_id
            or os.environ.get("IPHONE_MCP_BUNDLE_ID")
            or (previous.get("bundle_id") if previous.get("team") == team else None)
            or f"com.iphonemcp.{team.lower()}.wda"
        )

        print(f"→ iPhone:    {dev['name']} ({dev['udid']}), iOS {dev.get('os') or '?'}")
        print(f"→ Team:      {team}")
        print(f"→ Bundle ID: {bundle_id}")
        ensure_source()
        build(dev["udid"], team, bundle_id)
        paths.save_device_setup(
            dev["udid"],
            name=dev.get("name"),
            team=team,
            bundle_id=bundle_id,
            derived_data=str(paths.WDA_DERIVED),
            wda_ref=WDA_REF,
            runner_bundle_id=f"{bundle_id}.xctrunner",
            launcher="xcodebuild",
        )
    except SetupError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1

    print("✓ Done. On the iPhone, if not already done:")
    print("  • Settings → Privacy & Security → Developer Mode: on")
    print("  • Settings → Developer → Enable UI Automation: on")
    print("  • First start only: Settings → General → VPN & Device Management → trust the developer app")
    print("Optional, for Wi-Fi: Xcode → Window → Devices and Simulators → your iPhone → 'Connect via network'.")
    if hint := several_iphones_hint(dev["udid"]):
        print(hint)
    print(SPONSOR_LINE)
    return 0
