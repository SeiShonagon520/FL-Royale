"""Export-oriented variant of V4's existing fixed-shape shadow update.

This copies the existing CUDA-Graph branch into a separate class to make it
available on CPU for a bounded equivalence/export experiment. It is not yet an
Android implementation.
"""

from __future__ import annotations

import torch

from native_runner.training.v4.decoding import ShadowCandidateLegality
from native_runner.training.v4.tensors import CANDIDATE_ABILITY, CANDIDATE_DEPLOY


class MobileShadowCandidateLegality(ShadowCandidateLegality):
    def apply(self, rows, candidate_index, target_cell, delay_offset_bin, *, step):
        chosen = candidate_index.clamp(0, self.base.mask.shape[1] - 1)
        chosen_column = chosen[:, None]
        selected_cost = self.base.cost.gather(1, chosen_column)[:, 0]
        self.remaining_elixir -= selected_cost * rows.to(selected_cost.dtype)
        self.used_candidate |= rows[:, None] & (self._candidate_rows[None] == chosen_column)
        self.selected_count += rows.to(self.selected_count.dtype)
        variant = self.base.variant.gather(1, chosen_column)[:, 0]
        self.used_ability |= rows & (variant == CANDIDATE_ABILITY)
        own_row = self.base.own_card_row.gather(1, chosen_column)[:, 0]
        use_hand = rows & (variant == CANDIDATE_DEPLOY) & (own_row >= 0)
        self.used_hand_row |= use_hand[:, None] & (self._hand_rows[None] == own_row[:, None])
        exclusion = self.base.exclusion_group_id.gather(1, chosen_column)[:, 0]
        self.used_exclusion[:, step] = torch.where(rows, exclusion, self.used_exclusion[:, step])
        offset = torch.zeros_like(self.previous_delay_offset_ms)
        for offset_bin, milliseconds in enumerate(self.config.delay_offset_ms):
            offset = torch.where(delay_offset_bin == offset_bin, float(milliseconds), offset)
        self.previous_delay_offset_ms = torch.where(rows, offset, self.previous_delay_offset_ms)
        self.previous_delay_offset_bin = torch.where(rows, delay_offset_bin, self.previous_delay_offset_bin)

        selected_building = self.base.is_building.gather(1, chosen_column)[:, 0]
        building = rows & selected_building & (target_cell >= 0)
        safe_target = target_cell.clamp(0, self.config.board_width * self.config.board_height - 1)
        x = safe_target.remainder(self.config.board_width)
        y = torch.div(safe_target, self.config.board_width, rounding_mode="floor")
        planned_index = self.planned_count.clamp_max(self.config.max_micro_actions - 1)
        planned_x = x.to(self.planned_x.dtype) + 0.5 + self.base.building_offset_x.gather(1, chosen_column)[:, 0]
        planned_y = y.to(self.planned_y.dtype) + 0.5 + self.base.building_offset_y.gather(1, chosen_column)[:, 0]
        half_width = self.base.building_half_width.gather(1, chosen_column)[:, 0]
        half_height = self.base.building_half_height.gather(1, chosen_column)[:, 0]
        for planned_slot in range(self.config.max_micro_actions):
            write = building & (planned_index == planned_slot)
            self.planned_x[:, planned_slot] = torch.where(write, planned_x, self.planned_x[:, planned_slot])
            self.planned_y[:, planned_slot] = torch.where(write, planned_y, self.planned_y[:, planned_slot])
            self.planned_half_width[:, planned_slot] = torch.where(
                write, half_width, self.planned_half_width[:, planned_slot]
            )
            self.planned_half_height[:, planned_slot] = torch.where(
                write, half_height, self.planned_half_height[:, planned_slot]
            )
        self.planned_count += building.to(self.planned_count.dtype)
