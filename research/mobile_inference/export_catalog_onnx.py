"""Export one strict-load V4 checkpoint to the verified fixed Android ORT ABI.

All generated files stay in this project. Firstlight_CR is read-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import time
import traceback

import numpy as np
import onnx
import onnxruntime as ort
import torch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
from build_config import load
SOURCE = load()["firstlight_cr"]
sys.path.insert(0, str(SOURCE))

from mobile_shadow import MobileShadowCandidateLegality
from probe_export import FlatPolicy, flatten
from schema import representative_batch


ROOT = pathlib.Path(__file__).resolve().parents[2]
BUILD = ROOT / "build" / "mobile_inference" / "models"
GENERAL_BINDING = ROOT / "research" / "runtime_fallback" / "binding_fullshape_uint8.json"
SCHEMA = pathlib.Path(__file__).with_name("production_tensor_schema_uint8.json")
CHECKPOINTS = {
    "general": SOURCE / "checkpoints" / "General" / "checkpoint-step-00000460.pt",
    "active_il": SOURCE / "checkpoints" / "active IL" / "checkpoint-step-00000030.pt",
    "il": SOURCE / "checkpoints" / "IL" / "checkpoint-step-00029396.pt",
    "hog26_1": SOURCE / "checkpoints" / "2_6hog_expert" / "hog26-specialist1.pt",
    "hog26_2": SOURCE / "checkpoints" / "2_6hog_expert" / "hog26-specialist2.pt",
}


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def patch_bool_where(model: onnx.ModelProto) -> int:
    inferred = onnx.shape_inference.infer_shapes(model)
    value_types = {
        value.name: value.type.tensor_type.elem_type
        for value in (*inferred.graph.input, *inferred.graph.value_info, *inferred.graph.output)
        if value.type.HasField("tensor_type")
    }
    rewritten = []
    count = 0
    for node in inferred.graph.node:
        if node.op_type != "Where" or value_types.get(node.output[0]) != onnx.TensorProto.BOOL:
            rewritten.append(node)
            continue
        count += 1
        condition, a, b = node.input
        prefix = node.name or f"bool_where_{count}"
        inverted = prefix + "_inverse"
        selected_a = prefix + "_selected_a"
        selected_b = prefix + "_selected_b"
        rewritten.extend((
            onnx.helper.make_node("Not", [condition], [inverted], name=prefix + "_not"),
            onnx.helper.make_node("And", [condition, a], [selected_a], name=prefix + "_and_a"),
            onnx.helper.make_node("And", [inverted, b], [selected_b], name=prefix + "_and_b"),
            onnx.helper.make_node("Or", [selected_a, selected_b], list(node.output), name=prefix + "_or"),
        ))
    inferred.graph.ClearField("node")
    inferred.graph.node.extend(rewritten)
    model.CopyFrom(inferred)
    return count


def run(name: str) -> dict:
    started = time.monotonic()
    BUILD.mkdir(parents=True, exist_ok=True)
    from native_runner.training.v4.policy_session import load_policy_v4
    import native_runner.training.v4.model as model_module

    loaded = load_policy_v4(CHECKPOINTS[name], device="cpu")
    model = loaded.model
    for module in model.modules():
        module._non_persistent_buffers_set.clear()
    batch = representative_batch()
    leaves: list[torch.Tensor] = []
    flatten(batch, leaves)
    leaves = [item.to(torch.uint8) if item.dtype == torch.bool else item for item in leaves]
    state = model.initial_state(1)
    args = tuple(leaves) + (state.hidden, state.cell, torch.tensor([1], dtype=torch.uint8))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert len(args) == len(schema["inputs"]) == 92
    for index, (arg, spec) in enumerate(zip(args, schema["inputs"], strict=True)):
        if list(arg.shape) != spec["shape"] or str(arg.dtype).removeprefix("torch.") != spec["dtype"]:
            raise ValueError(f"Input {index} does not match Android ABI")

    previous_shadow = model_module.ShadowCandidateLegality
    model_module.ShadowCandidateLegality = MobileShadowCandidateLegality
    try:
        wrapper = FlatPolicy(model, batch, "act", uint8_masks=True).eval()
        with torch.no_grad():
            eager = wrapper(*args)
            exported = torch.export.export(wrapper, args, strict=False)
            graph_outputs = exported.module()(*args)
        graph_diff = [float((a.float() - b.float()).abs().max()) for a, b in zip(eager, graph_outputs, strict=True)]
        if max(graph_diff) != 0:
            raise ValueError(f"torch.export parity failed: {graph_diff}")

        raw = BUILD / f"v4_{name}_fullshape_uint8.onnx"
        torch.onnx.export(exported, args=args, f=str(raw), dynamo=True,
                          opset_version=18, external_data=True, optimize=False, verify=False)
        onnx.checker.check_model(str(raw))
        graph = onnx.load(str(raw), load_external_data=True)
        rewrites = patch_bool_where(graph)
        final = BUILD / f"v4_{name}_fullshape_uint8_embedded.onnx"
        onnx.save_model(graph, str(final), save_as_external_data=False)
        onnx.checker.check_model(str(final))
    finally:
        model_module.ShadowCandidateLegality = previous_shadow

    binding = json.loads(GENERAL_BINDING.read_text(encoding="utf-8"))
    graph_inputs = list(graph.graph.input)
    graph_outputs = list(graph.graph.output)
    if len(graph_inputs) != 92 or len(graph_outputs) != 8:
        raise ValueError("ONNX graph does not have the fixed 92->8 ABI")
    for index, (node, expected) in enumerate(zip(graph_inputs, binding["inputs"], strict=True)):
        if node.name != expected["onnx_name"]:
            raise ValueError(f"Input binding name changed at {index}")
    for index, (node, expected) in enumerate(zip(graph_outputs, binding["outputs"], strict=True)):
        if node.name != expected["onnx_name"]:
            raise ValueError(f"Output binding name changed at {index}")

    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.log_severity_level = 3
    session = ort.InferenceSession(str(final), options, providers=["CPUExecutionProvider"])
    feeds = {node.name: arg.detach().cpu().numpy()
             for node, arg in zip(graph_inputs, args, strict=True)}
    actual = session.run(None, feeds)
    integer_equal = [bool(np.array_equal(expected.detach().cpu().numpy(), got))
                     for expected, got in zip(eager[:6], actual[:6], strict=True)]
    state_max_diff = [float(np.max(np.abs(expected.detach().cpu().numpy() - got)))
                      for expected, got in zip(eager[6:], actual[6:], strict=True)]
    if not all(integer_equal) or max(state_max_diff) > 1e-5:
        raise ValueError(f"ORT parity failed: integer={integer_equal}, LSTM={state_max_diff}")

    return {
        "name": name,
        "checkpoint": str(loaded.checkpoint_path),
        "checkpoint_sha256": loaded.checkpoint_sha256,
        "checkpoint_id": loaded.checkpoint_id,
        "training_stage": loaded.training_stage,
        "update_step": loaded.update_step,
        "model": str(final),
        "model_sha256": sha256(final),
        "model_bytes": final.stat().st_size,
        "torch": torch.__version__,
        "onnxruntime": ort.__version__,
        "graph_nodes": len(list(exported.graph.nodes)),
        "bool_where_rewrites": rewrites,
        "same_general_binding": True,
        "integer_outputs_equal": integer_equal,
        "state_max_abs_diff": state_max_diff,
        "gate": actual[0].tolist(),
        "micro_action_count": actual[1].tolist(),
        "seconds": round(time.monotonic() - started, 3),
    }


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("name", choices=sorted(CHECKPOINTS))
    options = parser.parse_args()
    try:
        result = run(options.name)
        result["success"] = True
    except Exception as error:
        result = {"name": options.name, "success": False,
                  "error_type": type(error).__name__, "error": str(error),
                  "traceback_tail": traceback.format_exc()[-6000:]}
    destination = BUILD / f"v4_{options.name}_export_result.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("RESULT " + json.dumps(result), flush=True)
    if not result["success"]:
        raise SystemExit(1)
