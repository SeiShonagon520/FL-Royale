"""Build FL Royale from a Firstlight CR offline APK and local game resources."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import urllib.request
import uuid

from build_config import ROOT, load


def run(*args: object, cwd: Path = ROOT) -> None:
    command = [str(arg) for arg in args]
    print("+ " + subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def download(url: str, target: Path) -> None:
    if target.is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".download")
    print(f"Downloading {target.name}", flush=True)
    with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as output:
        shutil.copyfileobj(response, output)
    temporary.replace(target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--export-models", action="store_true")
    parser.add_argument("--keep-build-work", action="store_true")
    args = parser.parse_args()
    if args.config:
        os.environ["FL_BUILD_CONFIG"] = str(args.config.resolve())
    config = load()
    os.environ["JAVA_HOME"] = str(config["java_home"])
    os.environ["ANDROID_HOME"] = str(config["android_sdk"])
    os.environ["FL_PYTHON"] = sys.executable
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["PATH"] = str(config["java_home"] / "bin") + os.pathsep + os.environ["PATH"]
    output = args.output.resolve() if args.output else ROOT / "dist/fl-royale-1.8-r1.apk"
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}. Choose another --output path.")
    if not config["base_apk"].is_file():
        raise FileNotFoundError(f"Build the Firstlight CR offline APK first: {config['base_apk']}")
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    python = sys.executable
    java = config["java_home"] / "bin/java.exe"
    keytool = config["java_home"] / "bin/keytool.exe"
    apktool = config["apktool"]
    sdk_tools = config["build_tools"]
    download("https://github.com/iBotPeaches/Apktool/releases/download/v2.12.1/apktool_2.12.1.jar", apktool)
    download("https://repo.maven.apache.org/maven2/com/microsoft/onnxruntime/onnxruntime-android/1.30.0/onnxruntime-android-1.30.0.aar",
             build / "tools/onnxruntime-android-1.30.0.aar")

    run(python, "-B", ROOT / "tools/generate_local_cards.py")
    run(python, "-B", ROOT / "tools/stage_mobile_semantics.py")
    run(python, "-B", ROOT / "tools/export_models.py", *(["--force"] if args.export_models else []))
    run(python, "-B", ROOT / "tools/package_runtime_update.py")

    probe = ROOT / "baseline/native_runner/probe"
    library = build / "libcrprobe-local-app.so"
    compiler = config["ndk"] / "toolchains/llvm/prebuilt/windows-x86_64/bin/aarch64-linux-android24-clang++.cmd"
    run(compiler, "-std=c++17", "-O2", "-fPIC", "-fvisibility=hidden", "-Wall", "-Wextra",
        "-DFIRSTLIGHT_LOCAL_APP=1", "-DFIRSTLIGHT_ENABLE_COLD_CLONE_DIAGNOSTIC=1",
        "-shared", "-Wl,-z,max-page-size=16384", "-o", library,
        probe / "cr_replay_probe.cpp", probe / "battle_picker_bridge.cpp",
        probe / "native_touch_interceptor.cpp", probe / "native_call_arm64.S",
        probe / "remaining_runtime_arm64.S",
        ROOT / "research/snapshot_bootstrap_review/cold_clone_diagnostic.cpp", "-llog", "-ldl", "-lz")
    run(ROOT / "android_runtime_smoke/gradlew.bat", ":localapp:assembleDebug", "--console=plain",
        cwd=ROOT / "android_runtime_smoke")

    # A contributor uses their own signing identity. New builds reuse this key.
    key, password = config["keystore"], config["password_file"]
    if not key.exists():
        key.parent.mkdir(parents=True, exist_ok=True)
        password.parent.mkdir(parents=True, exist_ok=True)
        if not password.exists():
            password.write_text(secrets.token_urlsafe(32) + "\n", encoding="ascii")
        run(keytool, "-genkeypair", "-noprompt", "-keystore", key, "-alias", config["key_alias"],
            "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
            "-storepass:file", password, "-keypass:file", password, "-dname", "CN=FL Royale Local Build")

    work = build / ("apk-" + uuid.uuid4().hex)
    work.mkdir()
    decoded, runtime = work / "decoded", work / "runtime"
    runtime_apk = ROOT / "android_runtime_smoke/localapp/build/outputs/apk/debug/localapp-debug.apk"
    try:
        run(java, "-jar", apktool, "decode", config["base_apk"], "--output", decoded)
        shutil.copyfile(library, decoded / "lib/arm64-v8a/libcrprobe.so")
        run(java, "-jar", apktool, "decode", runtime_apk, "--no-res", "--output", runtime)
        run(python, "-B", ROOT / "tools/patch_ort_worker.py", "--decoded", decoded,
            "--smoke-decoded", runtime, "--receipt", work / "ort.json")
        run(python, "-B", ROOT / "resource_bootstrap/patch_decoded.py", "--decoded", decoded,
            "--receipt", work / "resources.json")
        run(python, "-B", ROOT / "tools/patch_local_runtime.py", "--decoded", decoded,
            "--runtime-decoded", runtime, "--receipt", work / "runtime.json")
        run(python, "-B", ROOT / "tools/patch_app_identity.py", "--decoded", decoded,
            "--receipt", work / "identity.json")
        run(java, "-jar", apktool, "build", decoded, "--output", work / "unsigned.apk")
        run(sdk_tools / "zipalign.exe", "-f", "-p", "4", work / "unsigned.apk", work / "aligned.apk")
        output.parent.mkdir(parents=True, exist_ok=True)
        run(java, "-jar", sdk_tools / "lib/apksigner.jar", "sign", "--ks", key,
            "--ks-key-alias", config["key_alias"], "--ks-pass", f"file:{password}",
            "--out", work / "signed.apk", work / "aligned.apk")
        shutil.move(work / "signed.apk", output)
        with output.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        report = {"release": "1.8-r1", "apk": output.name, "apk_sha256": digest,
                  "apk_bytes": output.stat().st_size, "package": "com.firstlight.offline",
                  "models": json.loads((build / "android_model_catalog.json").read_text())["models"]}
        output.with_suffix(".build.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"Built: {output}\nSHA-256: {digest}")
    finally:
        if not args.keep_build_work:
            # Delete only this invocation's directory, never an input/configured path.
            resolved = work.resolve()
            if resolved.parent == build.resolve() and resolved.name.startswith("apk-"):
                shutil.rmtree(resolved)


if __name__ == "__main__":
    main()
