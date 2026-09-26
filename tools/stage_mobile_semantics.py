"""Stage the exact reference FAIR implementation and reviewed NumPy tensor backend.

Only package-relative imports of the live adapter are copied. Training/model
weights are not imported. The source checkout is read-only; hashes bind every
staged module to it and to the explicit import-only backend transformation.
"""
import ast
import hashlib
import json
from pathlib import Path
import shutil

from build_config import load
ROOT = Path(__file__).resolve().parents[1]
SOURCE = load()["firstlight_cr"]
TARGET = ROOT / "build/mobile-semantics"
pending = ["native_runner.battle_env", "native_runner.training.tracking",
           "native_runner.training.v4.tensorizer", "native_runner.training.v4.decoding",
           "native_runner.training.v4.factory"]
seen = set()
manifest = []
while pending:
    module = pending.pop()
    if module in seen:
        continue
    seen.add(module)
    relative = Path(*module.split(".")).with_suffix(".py")
    source = SOURCE / relative
    if not source.is_file():
        continue
    text = source.read_text(encoding="utf-8")
    if module == "native_runner.training.v4.factory":
        tree = ast.parse(text)
        retain = {"ProductionSemanticBundleV4", "production_semantic_bundle", "deck_roles_from_form_availability",
                  "_public_tracker_v4", "build_episode_tensorizer_v4"}
        tree.body = [node for node in tree.body if
                     isinstance(node, (ast.Import,ast.ImportFrom)) and not (
                         isinstance(node,ast.ImportFrom) and node.module in {"model","resident_batch_vector","cr_native_env"})
                     or isinstance(node,(ast.ClassDef,ast.FunctionDef)) and node.name in retain]
        text = ast.unparse(tree)+"\n"
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.ImportFrom) and node.level:
            prefix = module.split(".")[:-node.level]
            child = ".".join(prefix + (node.module.split(".") if node.module else []))
            if child.startswith("native_runner."):
                pending.append(child)
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("native_runner."):
            pending.append(node.module)
    staged = text.replace("import torch\n", "import firstlight_numpy as torch\n").replace(
        "from torch import Tensor", "from firstlight_numpy import Tensor")
    destination = TARGET / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(staged, encoding="utf-8")
    for parent in destination.parents:
        if parent == TARGET:
            break
        (parent / "__init__.py").touch()
    manifest.append({"module": module, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                     "staged_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                     "numpy_backend": staged != text})
shutil.copytree(SOURCE / "native_runner/data/competitive", TARGET / "native_runner/data/competitive", dirs_exist_ok=True)
shutil.copyfile(SOURCE / "native_runner/data/native_match.json", TARGET / "native_runner/data/native_match.json")
for source in (ROOT / "on_device/python").glob("*.py"):
    shutil.copyfile(source, TARGET / source.name)
(TARGET / "source_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print(json.dumps({"modules": len(manifest), "backend_modules": sum(row["numpy_backend"] for row in manifest)}))
