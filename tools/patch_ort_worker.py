"""Inject the Android ONNX worker and exported models into the offline APK."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import shutil
import zipfile


PROJECT = pathlib.Path(__file__).resolve().parents[1]
BUILD = PROJECT / "build"
MODEL_IDS = ("general", "il", "active_il", "hog26_proactive", "hog26_measured")


def within_build(raw: str) -> pathlib.Path:
    path = pathlib.Path(raw).resolve(strict=True)
    try:
        path.relative_to(BUILD.resolve(strict=True))
    except ValueError as exc:
        raise ValueError(f"Input must be under {BUILD}: {path}") from exc
    if not path.is_dir():
        raise ValueError(f"Expected decoded directory: {path}")
    return path


def digest(path: pathlib.Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def exactly_one(paths: list[pathlib.Path], label: str) -> pathlib.Path:
    if len(paths) != 1:
        raise ValueError(f"Expected exactly one {label}, found {len(paths)}")
    return paths[0]


def copy_new(source: pathlib.Path, destination: pathlib.Path) -> None:
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def load_models() -> list[dict[str, object]]:
    catalog = json.loads((BUILD / "android_model_catalog.json").read_text(encoding="utf-8"))
    return [{**entry, "source": PROJECT / entry["source"]} for entry in catalog["models"]]


def patch(decoded: pathlib.Path, smoke_decoded: pathlib.Path) -> dict[str, object]:
    game_smali = decoded / "smali/com/supercell/clashroyale/GameApp.smali"
    game_original_hash = digest(game_smali)
    manifest = (decoded / "AndroidManifest.xml").read_text(encoding="utf-8")
    if 'package="nullsroyale.rel.free"' not in manifest or "ai.onnxruntime.TelemetryInitializer" in manifest:
        raise ValueError("Unexpected game manifest")

    game = game_smali.read_text(encoding="utf-8")
    anchor = (
        '    const-string v0, "crprobe"\n'
        '\n'
        '    invoke-static {v0}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V\n'
    )
    if game.count(anchor) != 1 or game.count(".method public final a()Z") != 1 or (
        '    const/4 v0, 0x0\n\n    return v0' not in game
    ):
        raise ValueError("GameApp.a() bootstrap anchor changed")

    ort_source = exactly_one(
        list(smoke_decoded.glob("smali*/ai/onnxruntime/OrtEnvironment.smali")),
        "ORT environment class",
    ).parent
    worker_source = exactly_one(
        list(smoke_decoded.glob("smali*/com/firstlight/offline/OnDeviceOrtPolicy.smali")),
        "shared policy worker class",
    ).parent
    bridge_source = exactly_one(
        list(smoke_decoded.glob("smali*/com/firstlight/offline/GameOrtBootstrap.smali")),
        "game bootstrap class",
    ).parent
    picker_source = exactly_one(
        list(smoke_decoded.glob("smali*/com/firstlight/offline/AiOpponentPicker.smali")),
        "AI opponent picker class",
    ).parent
    router_source = exactly_one(
        list(smoke_decoded.glob("smali*/com/firstlight/offline/BattlePickerRouter.smali")),
        "battle picker router class",
    ).parent
    catalog_source = exactly_one(
        list(smoke_decoded.glob("smali*/com/firstlight/offline/PolicyModelCatalog.smali")),
        "policy model catalog class",
    ).parent
    recurrent_source = exactly_one(
        list(smoke_decoded.glob("smali*/com/firstlight/offline/RecurrentPolicySession.smali")),
        "recurrent policy session class",
    ).parent
    loopback_source = exactly_one(
        list(smoke_decoded.glob("smali*/com/firstlight/offline/LoopbackProbeSession.smali")),
        "loopback probe session class",
    ).parent
    ort_classes = sorted(ort_source.glob("*.smali"))
    worker_classes = sorted(worker_source.glob("OnDeviceOrtPolicy*.smali"))
    bridge_classes = sorted(bridge_source.glob("GameOrtBootstrap*.smali"))
    picker_classes = sorted(picker_source.glob("AiOpponentPicker*.smali"))
    deck_classes = sorted(picker_source.glob("AiDeckSettings*.smali"))
    mode_classes = sorted(picker_source.glob("GameModePicker*.smali"))
    coordinator_classes = sorted(picker_source.glob("OfflineBattleCoordinator*.smali"))
    router_classes = sorted(router_source.glob("BattlePickerRouter*.smali"))
    catalog_classes = sorted(catalog_source.glob("PolicyModelCatalog*.smali"))
    recurrent_classes = sorted(recurrent_source.glob("RecurrentPolicySession*.smali"))
    loopback_classes = sorted(loopback_source.glob("LoopbackProbeSession*.smali"))
    offline_classes = sorted(
        path for path in picker_source.glob("*.smali")
        if not path.name.startswith("LocalBattleRuntime")
    )
    target_smali = decoded / "smali"
    sources = (*ort_classes, *offline_classes)
    for source in sources:
        package = "ai/onnxruntime" if source.parent == ort_source else "com/firstlight/offline"
        if any((smali_dir / package / source.name).exists() for smali_dir in decoded.glob("smali*")):
            raise ValueError(f"Game already contains {package}/{source.name}")

    aar = BUILD / "tools/onnxruntime-android-1.30.0.aar"
    models = load_models()
    schema = PROJECT / "research/mobile_inference/production_tensor_schema_uint8.json"
    binding = PROJECT / "research/runtime_fallback/binding_fullshape_uint8.json"
    catalog = PROJECT / "config/ai_catalog.json"
    with zipfile.ZipFile(aar) as archive:
        native_entries = {}
        for abi in ("arm64-v8a", "armeabi-v7a"):
            for name in ("libonnxruntime.so", "libonnxruntime4j_jni.so"):
                entry = f"jni/{abi}/{name}"
                native_entries[f"lib/{abi}/{name}"] = archive.read(entry)

    yml_path = decoded / "apktool.yml"
    yml = yml_path.read_text(encoding="utf-8")
    if yml.count("  minSdkVersion: 21\n") != 1:
        raise ValueError("Expected base minSdkVersion 21")
    for destination in (
        *(decoded / "assets" / model["asset"] for model in models),
        decoded / "assets/firstlight/abi" / schema.name,
        decoded / "assets/firstlight/abi" / binding.name,
        decoded / "assets/firstlight/ai_catalog.json",
    ):
        if destination.exists():
            raise ValueError(f"Game already contains {destination}")
    for relative in native_entries:
        if (decoded / relative).exists():
            raise ValueError(f"Game already contains {relative}")

    # All structural checks above precede writes. Apktool will assemble the
    # worker and bridge into the existing game DEX without a manifest merge.
    for source in sources:
        package = "ai/onnxruntime" if source.parent == ort_source else "com/firstlight/offline"
        copy_new(source, target_smali / package / source.name)
    for model in models:
        copy_new(model["source"], decoded / "assets" / model["asset"])
    copy_new(schema, decoded / "assets/firstlight/abi" / schema.name)
    copy_new(binding, decoded / "assets/firstlight/abi" / binding.name)
    copy_new(catalog, decoded / "assets/firstlight/ai_catalog.json")
    for relative, data in native_entries.items():
        destination = decoded / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

    insertion = anchor + (
        "    invoke-static {p0}, Lcom/firstlight/offline/GameOrtBootstrap;->start(Landroid/content/Context;)V\n"
    )
    game_smali.write_text(game.replace(anchor, insertion, 1), encoding="utf-8", newline="\n")
    yml_path.write_text(yml.replace("  minSdkVersion: 21\n", "  minSdkVersion: 24\n", 1),
                        encoding="utf-8", newline="\n")

    return {
        "schema": "firstlight.ort-game-patch.v1",
        "game_app_original_sha256": game_original_hash,
        "game_app_patched_sha256": digest(game_smali),
        "ort_classes": len(ort_classes),
        "worker_classes": len(worker_classes),
        "bridge_classes": len(bridge_classes),
        "picker_classes": len(picker_classes),
        "router_classes": len(router_classes),
        "catalog_classes": len(catalog_classes),
        "recurrent_classes": len(recurrent_classes),
        "loopback_classes": len(loopback_classes),
        "model_catalog_sha256": digest(BUILD / "android_model_catalog.json"),
        "models": [{key: model[key] for key in ("id", "asset", "sha256", "bytes")}
                   for model in models],
        "schema_sha256": digest(schema),
        "binding_sha256": digest(binding),
        "catalog_sha256": digest(catalog),
        "native_lib_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in native_entries.items()},
        "min_sdk": 24,
        "manifest_new_permissions_or_providers": 0,
        "boot_hook": "com.supercell.clashroyale.GameApp.a() -> GameOrtBootstrap.start(Context)",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decoded", required=True)
    parser.add_argument("--smoke-decoded", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    receipt = patch(within_build(args.decoded), within_build(args.smoke_decoded))
    pathlib.Path(args.receipt).write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print("Patched Android ONNX runtime and five model assets")


if __name__ == "__main__":
    main()
