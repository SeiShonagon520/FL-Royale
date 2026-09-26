"""Export the five Firstlight CR checkpoints and generate the Android catalog."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from build_config import ROOT

MODELS = (
    ("general", "general"),
    ("il", "il"),
    ("active_il", "active_il"),
    ("hog26_proactive", "hog26_1"),
    ("hog26_measured", "hog26_2"),
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Export again even if the model exists")
    args = parser.parse_args()
    entries = []
    for model_id, name in MODELS:
        model = ROOT / f"build/mobile_inference/models/v4_{name}_fullshape_uint8_embedded.onnx"
        if args.force or not model.is_file():
            subprocess.run([sys.executable, "-B", str(ROOT / "research/mobile_inference/export_catalog_onnx.py"), name],
                           cwd=ROOT, check=True)
        with model.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        entries.append({"id": model_id, "asset": f"firstlight/models/{model.name}",
                        "source": model.relative_to(ROOT).as_posix(),
                        "sha256": digest, "bytes": model.stat().st_size})
    catalog = {"schema": "firstlight-v4-android-ort-catalog.v1", "input_count": 92, "output_count": 8,
               "abi_schema_asset": "firstlight/abi/production_tensor_schema_uint8.json",
               "abi_binding_asset": "firstlight/abi/binding_fullshape_uint8.json", "models": entries}
    (ROOT / "build/android_model_catalog.json").write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    cases = []
    for entry in entries:
        cases.append(f'            case "{entry["id"]}": return new OnDeviceOrtPolicy.ModelSpec('
                     f'"{entry["asset"]}", "{entry["sha256"]}", SCHEMA, BINDING);')
    java = '''package com.firstlight.offline;

// Generated from this build's exported ONNX files by tools/export_models.py.
public final class PolicyModelCatalog {
    private static final String SCHEMA = "firstlight/abi/production_tensor_schema_uint8.json";
    private static final String BINDING = "firstlight/abi/binding_fullshape_uint8.json";
    private PolicyModelCatalog() {}
    public static OnDeviceOrtPolicy.ModelSpec forId(String id) {
        if (id == null) throw new IllegalArgumentException("Missing opponent id");
        switch (id) {
CASES
            default: throw new IllegalArgumentException("Unknown opponent id: " + id);
        }
    }
}
'''.replace("CASES", "\n".join(cases))
    target = ROOT / "build/generated-java/com/firstlight/offline/PolicyModelCatalog.java"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(java, encoding="utf-8")
    print("Prepared five ONNX models and the Android catalog")


if __name__ == "__main__":
    main()
