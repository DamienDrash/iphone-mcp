"""Finding and keeping a connection to WebDriverAgent on the iPhone.

Order of attempts:
1. IPHONE_MCP_WDA_URL (fixed address, works on any OS)
2. Xcode's CoreDevice tunnel (macOS; same address over cable *and* Wi-Fi)
3. usbmux port forwarding (USB, or Wi-Fi sync)
4. Last known Wi-Fi IP of the iPhone
"""

from __future__ import annotations

import asyncio
import glob
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
from pathlib import Path

from . import paths
from .wda import WDAClient, WDAError

WDA_PORT = 8100
KEEP_AWAKE_INTERVAL = 10  # seconds; iOS Auto-Lock is at least 30 s
NOT_CONNECTED = (
    "No connection to WebDriverAgent on the iPhone. Call iphone_start_agent first "
    "(iPhone on USB or on the same Wi-Fi as this computer, unlocked)."
)


def _free_port(preferred: int) -> int:
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("No free local port")


def url_host(ip: str) -> str:
    return f"[{ip}]" if ":" in ip else ip


async def coredevice_devices() -> list[dict]:
    """Physical iOS devices known to Xcode (`xcrun devicectl list devices`). Empty off macOS."""
    if not paths.IS_MAC or not shutil.which("xcrun"):
        return []
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp:
        try:
            proc = await asyncio.create_subprocess_exec(
                "xcrun",
                "devicectl",
                "list",
                "devices",
                "--json-output",
                tmp.name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), 30)
            data = json.loads(Path(tmp.name).read_text())
        except Exception:
            return []
    result = []
    for dev in data.get("result", {}).get("devices", []):
        hw = dev.get("hardwareProperties", {})
        if hw.get("platform") != "iOS" or hw.get("reality") != "physical":
            continue
        dp = dev.get("deviceProperties") or {}
        props = dict(dev.get("connectionProperties") or {})
        props.update(
            udid=hw.get("udid"),
            name=dp.get("name"),
            model=hw.get("marketingName"),
            os=dp.get("osVersionNumber"),
            developerMode=dp.get("developerModeStatus"),
        )
        result.append(props)
    return result


class Connection:
    """Holds exactly one live connection to WebDriverAgent."""

    def __init__(self) -> None:
        self.mode = os.environ.get("IPHONE_MCP_MODE", "auto").lower()  # auto | usb | wifi
        self.udid = os.environ.get("IPHONE_MCP_UDID") or paths.load(paths.WDA_FILE).get("udid")
        self.manual_url = os.environ.get("IPHONE_MCP_WDA_URL")
        self.client: WDAClient | None = None
        self.transport: str = "-"
        self._forwarder = None
        self._forward_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._proc: subprocess.Popen | None = None
        cfg = paths.load(paths.WDA_FILE)
        default_launcher = (
            "xcodebuild" if paths.IS_MAC and cfg.get("launcher", "xcodebuild") == "xcodebuild" else "pymobiledevice3"
        )
        self.launcher = os.environ.get("IPHONE_MCP_LAUNCHER", default_launcher).lower()
        self._xct = None  # XCTestLauncher when WebDriverAgent was started without Xcode
        self.keep_awake = os.environ.get("IPHONE_MCP_KEEP_AWAKE", "1").lower() not in ("0", "false", "no", "off")
        self._keep_awake_task: asyncio.Task | None = None

    # ---------- discovery ----------
    async def coredevice_info(self) -> dict | None:
        for dev in await coredevice_devices():
            if not self.udid or dev["udid"] == self.udid:
                return dev
        return None

    async def usbmux_devices(self) -> list:
        try:
            from pymobiledevice3 import usbmux

            devices = await usbmux.list_devices()
        except Exception:
            return []
        if self.udid:
            devices = [d for d in devices if d.serial == self.udid]
        return sorted(devices, key=lambda d: 0 if d.connection_type == "USB" else 1)

    async def _start_forward(self, serial: str, connection_type: str) -> int:
        from pymobiledevice3.tcp_forwarder import UsbmuxTcpForwarder

        await self._stop_forward()
        port = _free_port(WDA_PORT)
        ready = asyncio.Event()
        self._forwarder = UsbmuxTcpForwarder(
            serial, WDA_PORT, port, listening_event=ready, usbmux_connection_type=connection_type
        )
        self._forward_task = asyncio.create_task(self._forwarder.start())
        await asyncio.wait_for(ready.wait(), 5)
        return port

    async def _stop_forward(self) -> None:
        if self._forwarder is not None:
            try:
                self._forwarder.stop()
            except Exception:
                pass
        if self._forward_task is not None:
            self._forward_task.cancel()
        self._forwarder = self._forward_task = None

    async def _try(self, url: str, transport: str) -> WDAClient | None:
        client = WDAClient(url, timeout=30)
        try:
            st = await asyncio.wait_for(client.status(), 4)
        except (TimeoutError, WDAError):
            await client.close()
            return None
        ip = (st.get("ios") or {}).get("ip") if isinstance(st, dict) else None
        if ip:
            paths.update(paths.STATE_FILE, last_ip=ip)
        if self.client is not None and self.client is not client:
            await self.client.close()
        self.client, self.transport = client, transport
        self._ensure_keep_awake()
        return client

    # ---------- keep the screen awake ----------
    def _ensure_keep_awake(self) -> None:
        if self.keep_awake and (self._keep_awake_task is None or self._keep_awake_task.done()):
            self._keep_awake_task = asyncio.get_running_loop().create_task(self._keep_awake_loop())

    async def _keep_awake_loop(self) -> None:
        """Reset the iPhone's idle timer while this server is connected.

        A 1 ms touch just outside the screen counts as user activity for iOS but hits no UI
        (verified: unlike a status-bar tap it does not even scroll to top). Skipped while locked.
        """
        while self.keep_awake:
            await asyncio.sleep(KEEP_AWAKE_INTERVAL)
            client = self.client
            if client is None:
                continue
            try:
                if not await client.is_locked():
                    await client.tap(-5, -5, hold_ms=1)
            except Exception:  # noqa: BLE001 - connection hiccups are handled by get()
                pass

    def set_keep_awake(self, enabled: bool) -> None:
        self.keep_awake = enabled
        if enabled:
            if self.client is not None:
                self._ensure_keep_awake()
        elif self._keep_awake_task is not None:
            self._keep_awake_task.cancel()
            self._keep_awake_task = None

    # ---------- public ----------
    async def get(self) -> WDAClient:
        async with self._lock:
            if self.client is not None:
                try:
                    await asyncio.wait_for(self.client.status(), 4)
                    return self.client
                except (TimeoutError, WDAError):
                    await self.client.close()
                    self.client = None
                    await self._stop_forward()

            if self.manual_url:
                if c := await self._try(self.manual_url, f"fixed URL ({self.manual_url})"):
                    return c

            if self._xct is not None and self._xct.url:
                if c := await self._try(self._xct.url, self._xct.transport):
                    return c

            info = await self.coredevice_info()
            if info and info.get("tunnelState") == "connected" and info.get("tunnelIPAddress"):
                kind = "USB" if info.get("transportType") == "wired" else "Wi-Fi"
                allowed = self.mode == "auto" or (self.mode == "usb") == (kind == "USB")
                url = f"http://{url_host(info['tunnelIPAddress'])}:{WDA_PORT}"
                if allowed and (c := await self._try(url, f"{kind} (Xcode tunnel)")):
                    return c

            if self.mode in ("auto", "usb"):
                for dev in await self.usbmux_devices():
                    try:
                        port = await self._start_forward(dev.serial, dev.connection_type)
                    except Exception:
                        continue
                    label = "USB (usbmux)" if dev.connection_type == "USB" else "Wi-Fi (usbmux)"
                    if c := await self._try(f"http://127.0.0.1:{port}", label):
                        return c
                    await self._stop_forward()

            if self.mode in ("auto", "wifi"):
                ip = paths.load(paths.STATE_FILE).get("last_ip")
                if ip:
                    if c := await self._try(f"http://{url_host(ip)}:{WDA_PORT}", f"Wi-Fi ({ip})"):
                        return c

            raise WDAError(NOT_CONNECTED)

    # ---------- WebDriverAgent process (macOS only) ----------
    def wda_running_pid(self) -> int | None:
        if self._proc is not None:
            return self._proc.pid if self._proc.poll() is None else None
        pid = paths.load(paths.STATE_FILE).get("wda_pid")
        if not pid:
            return None
        try:
            os.kill(pid, 0)
            return pid
        except OSError:
            return None

    async def start_wda(self, timeout: float = 150) -> str:
        try:
            await self.get()
            return f"WebDriverAgent is already running, connected via {self.transport}."
        except WDAError:
            pass

        if self.launcher != "xcodebuild":
            return await self._start_without_xcode(timeout)
        cfg = paths.load(paths.WDA_FILE)
        udid = self.udid or cfg.get("udid")
        runs = sorted(glob.glob(os.path.join(cfg.get("derived_data") or "", "Build/Products/*.xctestrun")))
        if not runs or not udid:
            raise WDAError("WebDriverAgent is not built yet. Run once in a terminal: iphone-mcp setup")

        if self.wda_running_pid() and self._proc is None:
            await self.stop_wda()  # leftover xcodebuild from an earlier run without a reachable WDA

        def launch() -> None:
            log = open(paths.WDA_LOG, "a")
            self._proc = subprocess.Popen(
                ["xcodebuild", "test-without-building", "-xctestrun", runs[-1], "-destination", f"id={udid}"],
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            paths.update(paths.STATE_FILE, wda_pid=self._proc.pid)

        if not self.wda_running_pid():  # an xcodebuild of ours may still be waiting, e.g. for an unlock
            paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
            paths.WDA_LOG.write_text("")
            launch()
        attempts = 1
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            try:
                await self.get()
                return f"WebDriverAgent is running, connected via {self.transport}."
            except WDAError:
                log = paths.WDA_LOG.read_text(errors="ignore")
                if log.rfind("because the device is locked") > log.rfind("ServerURLHere"):
                    # xcodebuild keeps waiting and continues by itself once the iPhone is unlocked
                    raise WDAError(
                        "The iPhone is locked, so Xcode cannot start WebDriverAgent. Ask the user to unlock it "
                        "(passcode / Face ID), then call iphone_start_agent again."
                    ) from None
                if not self.wda_running_pid():
                    # e.g. the cable flaked or the Wi-Fi tunnel was still coming up: retry
                    if attempts >= 4:
                        tail = paths.WDA_LOG.read_text()[-1500:]
                        raise WDAError(f"xcodebuild exited {attempts} times. End of {paths.WDA_LOG}:\n{tail}") from None
                    attempts += 1
                    await asyncio.sleep(3)
                    launch()
                await asyncio.sleep(3)
        raise WDAError(f"WebDriverAgent not ready after {timeout:.0f}s. Log: {paths.WDA_LOG}")

    async def _start_without_xcode(self, timeout: float) -> str:
        """Launch through pymobiledevice3 (no Xcode; works on Linux and Windows too)."""
        from .xctest_launcher import XCTestLauncher

        cfg = paths.load(paths.WDA_FILE)
        if self._xct is None or not self._xct.running:
            if self._xct is not None:
                await self._xct.stop()
            self._xct = XCTestLauncher(
                udid=self.udid or cfg.get("udid"),
                runner_bundle_id=os.environ.get("IPHONE_MCP_RUNNER_BUNDLE_ID") or cfg.get("runner_bundle_id"),
                wifi=self.mode == "wifi",
            )
            try:
                await asyncio.wait_for(self._xct.start(), 60)
            except Exception as e:  # noqa: BLE001
                await self._xct.stop()
                self._xct = None
                hint = " Is the iPhone unlocked?" if "lock" in str(e).lower() else ""
                raise WDAError(f"Could not launch WebDriverAgent via pymobiledevice3: {e!r}.{hint}") from None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if (err := self._xct.runner_failed) is not None:
                await self._xct.stop()
                self._xct = None
                raise WDAError(
                    f"WebDriverAgent runner stopped: {err!r}. Unlock the iPhone and check that "
                    "Settings → Developer → Enable UI Automation is on."
                )
            try:
                await self.get()
                return f"WebDriverAgent is running (started without Xcode), connected via {self.transport}."
            except WDAError:
                await asyncio.sleep(2)
        raise WDAError(f"WebDriverAgent not ready after {timeout:.0f}s.")

    async def stop_wda(self) -> str:
        pid = self.wda_running_pid()
        if self.client is not None:
            await self.client.close()
            self.client = None
        await self._stop_forward()
        if self._xct is not None:
            await self._xct.stop()
            self._xct = None
            if not pid:
                return "WebDriverAgent stopped."
        if not pid:
            return "WebDriverAgent was not running (not started by iphone-mcp)."
        try:
            killpg = getattr(os, "killpg", None)  # not on Windows
            if killpg is None:
                raise OSError
            killpg(pid, signal.SIGTERM)
        except OSError:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        if self._proc is not None:
            try:
                self._proc.wait(5)
            except subprocess.TimeoutExpired:
                pass
        self._proc = None
        paths.update(paths.STATE_FILE, wda_pid=None)
        return "WebDriverAgent stopped."
