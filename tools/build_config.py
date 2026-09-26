"""Local paths shared by the public build tools."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def load() -> dict:
    path = Path(os.environ.get("FL_BUILD_CONFIG", ROOT / "build.local.json")).resolve()
    if not path.is_file():
        raise FileNotFoundError("Copy build.example.json to build.local.json and fill in your paths.")
    data = json.loads(path.read_text(encoding="utf-8-sig"))

    def local(value: str) -> Path:
        expanded = Path(os.path.expandvars(value)).expanduser()
        return (path.parent / expanded).resolve() if not expanded.is_absolute() else expanded.resolve()

    source = local(data["firstlight_cr"])
    sdk = local(data.get("android_sdk") or os.environ.get("ANDROID_HOME") or
                str(Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Android/Sdk"))
    java = data.get("java_home") or os.environ.get("JAVA_HOME")
    if not java:
        raise ValueError("Set java_home in build.local.json or set JAVA_HOME to JDK 17.")
    result = {
        "firstlight_cr": source,
        "base_apk": local(data["base_apk"]) if data.get("base_apk") else source / "build/cr-ai-offline-cache.apk",
        "runtime_update": local(data["runtime_update"]),
        "java_home": local(java),
        "android_sdk": sdk,
        "build_tools": sdk / "build-tools" / data.get("build_tools_version", "36.1.0"),
        "ndk": sdk / "ndk" / data.get("ndk_version", "27.3.13750724"),
        "python": Path(sys.executable),
        "apktool": ROOT / "build/tools/apktool_2.12.1.jar",
        "keystore": local(data["keystore"]) if data.get("keystore") else ROOT / "build/signing/fl-royale.keystore",
        "password_file": local(data["password_file"]) if data.get("password_file") else ROOT / "build/signing/password.txt",
        "key_alias": data.get("key_alias", "fl-royale"),
    }
    return result


if __name__ == "__main__":
    print(json.dumps(load(), default=str))
