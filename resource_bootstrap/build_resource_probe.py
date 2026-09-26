"""Build a separate-package APK to test first-run resource installation.

The output package is com.firstlight.resourceprobe. It cannot modify or
replace nullsroyale.rel.free or its app-private data.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile
import sys


APP = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
PACK = APP / "build" / "resource-pack"
WORK = APP / "build" / "resource-probe"
DIST = APP / "dist" / "firstlight-resource-probe.apk"
sys.path.insert(0, str(APP / "tools"))
from build_config import load
CONFIG = load()
SDK = CONFIG["android_sdk"]
TOOLS = CONFIG["build_tools"]
ANDROID_JAR = SDK / "platforms" / "android-34" / "android.jar"
TEST_PASSWORD = "resource-probe-test-key"


def run(*command: object) -> None:
    args = [str(item) for item in command]
    print("+ " + " ".join(args[:2]) + (" ..." if len(args) > 2 else ""), flush=True)
    subprocess.run(args, check=True, timeout=300)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    bundle = PACK / "runtime_update.zip"
    manifest = PACK / "runtime_update.asset_manifest.json"
    if not bundle.is_file():
        raise ValueError("Run tools/package_runtime_update.py --build first")
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    asset_dir = WORK / "assets" / "firstlight"
    asset_dir.mkdir(parents=True, exist_ok=True)
    asset_bundle = asset_dir / "runtime_update.zip"
    if asset_bundle.exists():
        asset_bundle.unlink()
    if not asset_bundle.exists():
        try:
            os.link(bundle, asset_bundle)
        except OSError:
            shutil.copyfile(bundle, asset_bundle)
    shutil.copyfile(manifest, asset_dir / "runtime_update.manifest.json")

    classes = WORK / "classes"
    dex = WORK / "dex"
    classes.mkdir(parents=True, exist_ok=True)
    dex.mkdir(parents=True, exist_ok=True)
    sources = sorted((HERE / "src").rglob("*.java"))
    run("javac", "-encoding", "UTF-8", "-source", "8", "-target", "8",
        "-cp", ANDROID_JAR, "-d", classes, *sources)
    run(TOOLS / "d8.bat", "--min-api", "21", "--lib", ANDROID_JAR,
        "--output", dex, *sorted(classes.rglob("*.class")))

    unsigned = WORK / "resource-probe-unsigned.apk"
    aligned = WORK / "resource-probe-aligned.apk"
    build_manifest = WORK / "AndroidManifest.xml"
    shutil.copyfile(HERE / "AndroidManifest.probe.xml", build_manifest)
    run(TOOLS / "aapt.exe", "package", "-f", "-M", build_manifest,
        "-I", ANDROID_JAR, "-A", WORK / "assets", "-F", unsigned, "-0", "zip")
    with zipfile.ZipFile(unsigned, "a") as archive:
        archive.write(dex / "classes.dex", "classes.dex", compress_type=zipfile.ZIP_DEFLATED)
    run(TOOLS / "zipalign.exe", "-f", "-p", "4", unsigned, aligned)

    key = WORK / "resource-probe.keystore"
    password = WORK / "resource-probe.keystore.pass"
    if not key.exists():
        password.write_text(TEST_PASSWORD + "\n", encoding="ascii")
        run("keytool", "-genkeypair", "-noprompt", "-keystore", key,
            "-alias", "resource-probe", "-keyalg", "RSA", "-keysize", "2048",
            "-validity", "3650", "-storepass", TEST_PASSWORD, "-keypass", TEST_PASSWORD,
            "-dname", "CN=Firstlight Resource Probe")
    DIST.parent.mkdir(parents=True, exist_ok=True)
    run(TOOLS / "apksigner.bat", "sign", "--ks", key, "--ks-key-alias", "resource-probe",
        "--ks-pass", f"file:{password}",
        "--out", DIST, aligned)
    run(TOOLS / "apksigner.bat", "verify", "--verbose", DIST)
    with zipfile.ZipFile(DIST) as archive:
        names = set(archive.namelist())
        required = {"classes.dex", "assets/firstlight/runtime_update.zip",
                    "assets/firstlight/runtime_update.manifest.json"}
        if not required <= names:
            raise ValueError(f"Probe APK missing assets: {required - names}")
    print(json.dumps({"path": str(DIST), "size": DIST.stat().st_size,
                      "sha256": sha256(DIST), "resource_sha256": sha256(bundle)}, indent=2))


if __name__ == "__main__":
    main()
