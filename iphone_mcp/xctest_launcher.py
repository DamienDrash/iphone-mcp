"""Launch WebDriverAgent without Xcode, through pymobiledevice3 (macOS, Linux, Windows).

How it works:
1. Open a tunnel to the iPhone. iOS 17.4+: pymobiledevice3's in-process *userspace* tunnel
   (no root), over USB or – with ``wifi=True`` – over the network via RemotePairing.
   Older iOS: plain lockdown over usbmux.
2. Make sure the Developer Disk Image is mounted.
3. Start the installed WebDriverAgent runner through Apple's XCUITest service.
4. Expose WebDriverAgent's port 8100 on 127.0.0.1 through a small local TCP forwarder,
   because the userspace tunnel is not reachable by normal OS sockets.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from typing import Any

log = logging.getLogger(__name__)
WDA_PORT = 8100


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class XCTestLauncher:
    def __init__(self, udid: str | None, runner_bundle_id: str | None, wifi: bool = False) -> None:
        self.udid = udid
        self.runner_bundle_id = runner_bundle_id
        self.wifi = wifi
        self.provider: Any = None
        self.url: str | None = None
        self.transport = "-"
        self._tunnel: Any = None
        self._runner: asyncio.Task | None = None
        self._server: asyncio.base_events.Server | None = None

    # ---------- tunnel ----------
    async def _open_provider(self) -> Any:
        from pymobiledevice3.lockdown import create_using_usbmux

        if not self.wifi:
            lockdown = await create_using_usbmux(self.udid)
            self.udid = self.udid or lockdown.udid
            version = tuple(int(p) for p in str(lockdown.product_version).split(".")[:2])
            if version < (17, 0):
                self.transport = f"{'Wi-Fi' if await self._via_network() else 'USB'} (lockdown, no Xcode)"
                return lockdown
        from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel

        self._tunnel = UserspaceRsdTunnel(serial=self.udid, remotepairing=self.wifi)
        rsd = await self._tunnel.aopen()
        self.udid = self.udid or rsd.udid
        self.transport = f"{'Wi-Fi' if self.wifi or await self._via_network() else 'USB'} (tunnel, no Xcode)"
        return rsd

    async def _via_network(self) -> bool:
        from pymobiledevice3 import usbmux

        with contextlib.suppress(Exception):
            for dev in await usbmux.list_devices():
                if dev.serial == self.udid:
                    return dev.connection_type != "USB"
        return False

    async def _mount_ddi(self) -> None:
        from pymobiledevice3.exceptions import AlreadyMountedError
        from pymobiledevice3.services.mobile_image_mounter import auto_mount

        try:
            await auto_mount(self.provider)
        except AlreadyMountedError:
            pass

    async def find_runner(self) -> str:
        """Bundle ID of an installed XCTest runner that looks like WebDriverAgent."""
        from pymobiledevice3.services.installation_proxy import InstallationProxyService

        apps = await InstallationProxyService(self.provider).get_apps(application_type="User")
        runners = [bid for bid in apps if bid.endswith(".xctrunner")]
        wda = [bid for bid in runners if "webdriveragent" in bid.lower() or "wda" in bid.lower()]
        if not (wda or runners):
            raise RuntimeError(
                "No WebDriverAgent runner is installed on the iPhone. Run `iphone-mcp setup` first "
                "(see the README section 'Linux & Windows')."
            )
        return (wda or runners)[0]

    # ---------- forwarder ----------
    async def _pipe_local_to_remote(self, reader: asyncio.StreamReader, remote: Any) -> None:
        while data := await reader.read(65536):
            await remote.sendall(data)

    async def _pipe_remote_to_local(self, remote: Any, writer: asyncio.StreamWriter) -> None:
        while data := await remote.recv_any(65536):
            writer.write(data)
            await writer.drain()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            remote = await self.provider.create_service_connection(WDA_PORT)
        except Exception as e:  # noqa: BLE001
            log.debug("cannot reach WDA port: %r", e)
            writer.close()
            return
        tasks = [
            asyncio.create_task(self._pipe_local_to_remote(reader, remote)),
            asyncio.create_task(self._pipe_remote_to_local(remote, writer)),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            with contextlib.suppress(Exception):
                await remote.close()
            with contextlib.suppress(Exception):
                writer.close()

    # ---------- public ----------
    async def start(self) -> str:
        """Start the runner and the local forwarder; returns the local WebDriverAgent URL."""
        from pymobiledevice3.services.dvt.testmanaged.xcuitest import TestConfig, XCUITestService

        self.provider = await self._open_provider()
        await self._mount_ddi()
        if not self.runner_bundle_id:
            self.runner_bundle_id = await self.find_runner()
        cfg = await TestConfig.create_for(self.provider, runner_bundle_id=self.runner_bundle_id)
        self._runner = asyncio.create_task(XCUITestService(self.provider).run(cfg), name="wda-runner")
        port = _free_port()
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", port)
        self.url = f"http://127.0.0.1:{port}"
        return self.url

    @property
    def runner_failed(self) -> BaseException | None:
        if self._runner is not None and self._runner.done():
            if self._runner.cancelled():
                return RuntimeError("runner cancelled")
            return self._runner.exception() or RuntimeError("WebDriverAgent runner exited")
        return None

    @property
    def running(self) -> bool:
        return self._runner is not None and not self._runner.done()

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
        if self._runner is not None:
            self._runner.cancel()
            with contextlib.suppress(BaseException):
                await self._runner
        if self._tunnel is not None:
            with contextlib.suppress(Exception):
                await self._tunnel.aclose()
        self._server = self._runner = self._tunnel = None
        self.url = None
