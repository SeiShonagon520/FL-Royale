"""Write ordered V4 Android tensor ABI from a real production tensorizer."""

from __future__ import annotations

import dataclasses
import argparse
import importlib.util
import json
import pathlib
import sys

import torch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
from build_config import load
SOURCE = load()["firstlight_cr"]
sys.path.insert(0, str(SOURCE))


def representative_batch():
    source = SOURCE / "native_runner" / "tests" / "test_training_v4_tensorizer.py"
    spec = importlib.util.spec_from_file_location("v4_tensorizer_test_fixture", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    specs, cards, abilities, archetypes = module._semantic_catalog()
    tensorizer = module._tensorizer(specs, cards, abilities, archetypes)
    batch = tensorizer.tensorize(module._observation(), validate=True)
    from native_runner.training.v4.policy_session import _fixed_cuda_graph_batch_v4

    return _fixed_cuda_graph_batch_v4(batch)


def flatten(value, prefix: str, result: list[dict], *, uint8_masks: bool):
    if isinstance(value, torch.Tensor):
        dtype = "uint8" if uint8_masks and value.dtype == torch.bool else str(value.dtype).replace("torch.", "")
        result.append({
            "index": len(result),
            "name": prefix,
            "dtype": dtype,
            "shape": list(value.shape),
            "bytes": value.element_size() * value.numel(),
        })
    elif dataclasses.is_dataclass(value):
        for field in dataclasses.fields(value):
            flatten(
                getattr(value, field.name),
                f"{prefix}.{field.name}" if prefix else field.name,
                result,
                uint8_masks=uint8_masks,
            )
    else:
        raise TypeError(type(value))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--uint8-masks", action="store_true")
    options = parser.parse_args()
    batch = representative_batch()
    tensors: list[dict] = []
    flatten(batch, "", tensors, uint8_masks=options.uint8_masks)
    tensors += [
        {"index": len(tensors), "name": "hidden", "dtype": "float32", "shape": [1, 512], "bytes": 2048},
        {"index": len(tensors) + 1, "name": "cell", "dtype": "float32", "shape": [1, 512], "bytes": 2048},
        {"index": len(tensors) + 2, "name": "episode_start", "dtype": "uint8" if options.uint8_masks else "bool", "shape": [1], "bytes": 1},
    ]
    outputs = [
        ("gate", "int64", [1]),
        ("micro_action_count", "int64", [1]),
        ("candidate_index", "int64", [1, 2]),
        ("candidate_uid", "int64", [1, 2]),
        ("target_cell", "int64", [1, 2]),
        ("delay_offset_bin", "int64", [1, 2]),
        ("next_hidden", "float32", [1, 512]),
        ("next_cell", "float32", [1, 512]),
    ]
    payload = {
        "contract": "universal-card-policy.v4 mobile export tensor ABI probe",
        "source": "production tensorizer fixture, with fixed effect/relation capacities",
        "uint8_masks": options.uint8_masks,
        "source_revision": "read source for exact contract; JSON is a generated probe",
        "inputs": tensors,
        "outputs": [
            {"index": i, "name": name, "dtype": dtype, "shape": shape}
            for i, (name, dtype, shape) in enumerate(outputs)
        ],
        "total_input_bytes": sum(item["bytes"] for item in tensors),
    }
    output = pathlib.Path(__file__).with_name(
        "production_tensor_schema_uint8.json" if options.uint8_masks else "production_tensor_schema.json"
    )
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "input_count": len(tensors), "input_bytes": payload["total_input_bytes"]}))


if __name__ == "__main__":
    main()
