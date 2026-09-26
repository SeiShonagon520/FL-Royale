"""Flatten the V4 policy inputs and outputs for Android ONNX export."""

from __future__ import annotations

import argparse
import copy
import dataclasses
import importlib.util
import json
import pathlib
import sys
import time
import traceback

import torch
from torch import nn


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
from build_config import load
SOURCE = load()["firstlight_cr"]
sys.path.insert(0, str(SOURCE))


def fixture_batch():
    path = SOURCE / "native_runner" / "tests" / "test_training_v4_model.py"
    spec = importlib.util.spec_from_file_location("v4_model_test_fixture", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._batch(1)


def flatten(value, leaves: list[torch.Tensor]):
    if isinstance(value, torch.Tensor):
        leaves.append(value)
    elif dataclasses.is_dataclass(value):
        for field in dataclasses.fields(value):
            flatten(getattr(value, field.name), leaves)
    else:
        raise TypeError(type(value))


def reconstruct(template, leaves, offset: int, uint8_masks: bool = False):
    if isinstance(template, torch.Tensor):
        value = leaves[offset]
        return (value.bool() if uint8_masks and template.dtype == torch.bool else value), offset + 1
    values = {}
    for field in dataclasses.fields(template):
        values[field.name], offset = reconstruct(getattr(template, field.name), leaves, offset, uint8_masks)
    return type(template)(**values), offset


class FlatPolicy(nn.Module):
    def __init__(self, model: nn.Module, template, mode: str, uint8_masks: bool = False):
        super().__init__()
        self.model = model
        self.template = template
        self.mode = mode
        self.uint8_masks = uint8_masks

    def forward(self, *inputs):
        from native_runner.training.v4.tensors import RecurrentPolicyStateV4

        batch, used = reconstruct(self.template, inputs, 0, self.uint8_masks)
        hidden, cell, episode_start = inputs[used : used + 3]
        if self.uint8_masks:
            episode_start = episode_start.bool()
        state = RecurrentPolicyStateV4(hidden=hidden, cell=cell)
        if self.mode == "core":
            context = self.model.forward(batch, state, episode_start=episode_start, validate=False)
            return (
                context.policy_context,
                context.next_state.hidden,
                context.next_state.cell,
            )
        result = self.model.act(batch, state, episode_start=episode_start, validate=False)
        action = result.actions
        return (
            action.gate,
            action.micro_action_count,
            action.candidate_index,
            action.candidate_uid,
            action.target_cell,
            action.delay_offset_bin,
            result.next_state.hidden,
            result.next_state.cell,
        )
