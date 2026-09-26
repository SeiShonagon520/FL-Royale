"""Add a verified runtime-update asset and first-launch gate to a decoded APK.

This script edits only an Apktool decoded directory inside the project build directory. It does
not touch the original Firstlight_CR tree or an installed Android package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET


APP = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
PACK = APP / "build" / "resource-pack"
PROBE_APK = APP / "dist" / "firstlight-resource-probe.apk"
PROBE_DECODED = APP / "build" / "resource-probe" / "decoded"
sys.path.insert(0, str(APP / "tools"))
from build_config import load
APKTOOL = load()["apktool"]
PACKAGE = "nullsroyale.rel.free"
ANDROID = "{http://schemas.android.com/apk/res/android}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def assert_decoded(path: Path) -> None:
    path = path.resolve()
    build = (APP / "build").resolve()
    if not path.is_relative_to(build) or path == build:
        raise ValueError("Decoded tree must be inside the project build directory")
    if not (path / "AndroidManifest.xml").is_file() or not (path / "apktool.yml").is_file():
        raise ValueError("Expected an Apktool decoded APK directory")


def prepare_smali() -> Path:
    subprocess.run([sys.executable, str(HERE / "build_resource_probe.py")], check=True, timeout=300)
    subprocess.run(["java", "-jar", str(APKTOOL), "decode", "-f", "-r", str(PROBE_APK),
                    "--output", str(PROBE_DECODED)], check=True, timeout=300)
    source = PROBE_DECODED / "smali" / "com" / "firstlight" / "offline"
    if not source.is_dir():
        raise ValueError("Resource bootstrap smali was not generated")
    return source


def patch_manifest(path: Path) -> None:
    manifest = path.read_text(encoding="utf-8")
    tree = ET.fromstring(manifest)
    if tree.get("package") != PACKAGE:
        raise ValueError("Unexpected APK package")
    application = tree.find("application")
    if application is None or application.get(ANDROID + "name") != "com.supercell.clashroyale.EngineClusterApplication":
        raise ValueError("Unexpected Android application entry")
    if "com.firstlight.offline.ResourceBootstrapActivity" in manifest:
        raise ValueError("Resource bootstrap activity already present")

    opening = re.search(
        r'(?m)^        <activity [^\n]*android:name="com\.supercell\.clashroyale\.GameApp"[^\n]*>\n',
        manifest,
    )
    if opening is None:
        raise ValueError("Native GameApp manifest anchor changed")
    launcher = (
        '            <intent-filter>\n'
        '                <action android:name="android.intent.action.MAIN"/>\n'
        '                <category android:name="android.intent.category.LAUNCHER"/>\n'
        '            </intent-filter>\n'
    )
    if not manifest[opening.end():].startswith(launcher) or manifest.count(launcher) != 1:
        raise ValueError("Expected exactly one GameApp launcher filter")
    bootstrap = (
        '        <activity android:exported="true" '
        'android:name="com.firstlight.offline.ResourceBootstrapActivity" '
        'android:screenOrientation="sensorPortrait" '
        'android:theme="@android:style/Theme.NoTitleBar.Fullscreen">\n'
        + launcher +
        '        </activity>\n'
    )
    updated = manifest[:opening.start()] + bootstrap + manifest[opening.start():]
    # Remove the filter immediately following GameApp, leaving its deep links intact.
    new_opening = updated.index(opening.group(0), opening.start() + len(bootstrap)) + len(opening.group(0))
    updated = updated[:new_opening] + updated[new_opening + len(launcher):]
    parsed = ET.fromstring(updated)
    activities = parsed.find("application").findall("activity")
    launcher_names = []
    for activity in activities:
        for intent in activity.findall("intent-filter"):
            actions = {entry.get(ANDROID + "name") for entry in intent.findall("action")}
            categories = {entry.get(ANDROID + "name") for entry in intent.findall("category")}
            if "android.intent.action.MAIN" in actions and "android.intent.category.LAUNCHER" in categories:
                launcher_names.append(activity.get(ANDROID + "name"))
    if launcher_names != ["com.firstlight.offline.ResourceBootstrapActivity"]:
        raise ValueError(f"Unexpected launchers after patch: {launcher_names}")
    path.write_text(updated, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decoded", type=Path, required=True)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    decoded = args.decoded.resolve()
    assert_decoded(decoded)
    bundle = PACK / "runtime_update.zip"
    asset_manifest = PACK / "runtime_update.asset_manifest.json"
    if not bundle.is_file() or not asset_manifest.is_file():
        raise ValueError("Resource pack missing or changed; rebuild it first")
    smali = prepare_smali()
    target_smali = decoded / "smali_classes2" / "com" / "firstlight" / "offline"
    target_smali.mkdir(parents=True, exist_ok=True)
    names = sorted(path for path in smali.glob("*.smali")
                   if path.name.startswith("RuntimeUpdateInstaller")
                   or path.name.startswith("ResourceBootstrapActivity"))
    for source in names:
        destination = target_smali / source.name
        if destination.exists():
            raise ValueError(f"Bootstrap smali collision: {destination}")
        shutil.copyfile(source, destination)

    asset_target = decoded / "assets" / "firstlight"
    asset_target.mkdir(parents=True, exist_ok=True)
    for source, name in ((bundle, "runtime_update.zip"),
                         (asset_manifest, "runtime_update.manifest.json")):
        destination = asset_target / name
        if destination.exists():
            raise ValueError(f"Bootstrap asset collision: {destination}")
        shutil.copyfile(source, destination)

    config = decoded / "apktool.yml"
    config_text = config.read_text(encoding="utf-8")
    if config_text.count("doNotCompress:\n") != 1:
        raise ValueError("Apktool compression configuration changed")
    config.write_text(config_text.replace("doNotCompress:\n",
            "doNotCompress:\n- assets/firstlight/runtime_update.zip\n", 1), encoding="utf-8")
    patch_manifest(decoded / "AndroidManifest.xml")
    resource_metadata = json.loads(asset_manifest.read_text(encoding="utf-8"))
    receipt = {
        "schema": "firstlight.runtime-resource-patch.v1",
        "runtime_content_version": "15.535.86",
        "resource_files": resource_metadata["file_count"],
        "resource_uncompressed_bytes": resource_metadata["uncompressed_bytes"],
        "bundle_sha256": sha256(bundle),
        "asset_manifest_sha256": sha256(asset_target / "runtime_update.manifest.json"),
        "smali_classes": len(names),
        "launcher": "com.firstlight.offline.ResourceBootstrapActivity",
        "game_activity": "com.supercell.clashroyale.GameApp",
    }
    if args.receipt:
        target = args.receipt.resolve()
        if not target.is_relative_to((APP / "build").resolve()):
            raise ValueError("Patch receipt must stay within the project build directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(f"Patched {decoded}: {len(names)} smali files, 2 assets, one launcher gate")


if __name__ == "__main__":
    main()
