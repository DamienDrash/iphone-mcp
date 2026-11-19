import plistlib
import zipfile

import pytest

from iphone_mcp import __version__, cli, paths, setup_wda


def test_version(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert __version__ in capsys.readouterr().out


def test_setup_off_macos_uses_prebuilt(monkeypatch, tmp_path):
    fake = tmp_path / "unsigned.ipa"
    calls = []
    monkeypatch.setattr(paths, "IS_MAC", False)
    monkeypatch.setattr(setup_wda, "download_prebuilt", lambda: calls.append(1) or fake)
    assert setup_wda.run() == 0
    assert calls == [1]


def test_ipa_bundle_id(tmp_path):
    ipa = tmp_path / "x.ipa"
    with zipfile.ZipFile(ipa, "w") as z:
        z.writestr("Payload/Runner.app/Info.plist", plistlib.dumps({"CFBundleIdentifier": "com.example.wda.xctrunner"}))
    assert setup_wda.ipa_bundle_id(ipa) == "com.example.wda.xctrunner"


def test_ipa_bundle_id_rejects_non_app(tmp_path):
    bad = tmp_path / "bad.ipa"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("readme.txt", "nope")
    with pytest.raises(setup_wda.SetupError):
        setup_wda.ipa_bundle_id(bad)
