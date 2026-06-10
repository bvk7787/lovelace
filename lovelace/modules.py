"""Module type IDs, block positions, and module builder helpers."""

from __future__ import annotations

import json
from pathlib import Path

# ── Module type IDs ──────────────────────────────────────────────────────────

LFO = 5
SEQUENCER = 4
SAMPLE_AND_HOLD = 10
CV_INVERT = 17
MULTIPLIER = 22
QUANTIZER = 28
IN_SWITCH = 31
OUT_SWITCH = 32
RANDOM = 39
STOMPSWITCH = 44
VALUE = 45
CV_DELAY = 46
CLOCK_DIVIDER = 49
COMPARATOR = 50
CV_RECTIFY = 51
PIXEL = 81
EURO_CV_OUT_4 = 87
EURO_CV_OUT_1 = 99
EURO_CV_OUT_2 = 100
EURO_CV_OUT_3 = 101
CV_MIXER = 104

# ── Block positions (absolute, used in connections) ──────────────────────────
# These are the block's "position" values in ModuleIndex, which are the
# absolute block indices used in the binary connection records.

class B:
    """Block position namespaces per module type."""

    class LFO:
        cv_control = 0
        tap_control = 1
        swing_amount = 2
        output = 3
        phase_input = 4
        phase_reset = 5

    class Sequencer:
        # step_N = N-1 for N in 1..32
        gate_in = 32
        queue_start = 33
        key_input_note = 34
        key_input_gate = 35
        out_track_1 = 36
        out_track_2 = 37
        out_track_3 = 38
        out_track_4 = 39
        out_track_5 = 40
        out_track_6 = 41
        out_track_7 = 42
        out_track_8 = 43

    class SH:
        cv_input = 0
        trigger = 1
        cv_output = 2

    class CVInvert:
        cv_input = 0
        cv_output = 1

    class Multiplier:
        cv_input_1 = 0
        cv_input_2 = 1
        cv_input_3 = 2
        cv_input_4 = 3
        cv_input_5 = 4
        cv_input_6 = 5
        cv_input_7 = 6
        cv_input_8 = 7
        cv_output = 8

    class Quantizer:
        cv_input = 0
        cv_output = 1
        key = 2
        scale = 3

    class InSwitch:
        # cv_input_N = N-1 for N in 1..16
        in_select = 16
        cv_output = 17

    class OutSwitch:
        cv_input = 0
        out_select = 1
        # cv_output_N = N+1 for N in 1..16

    class Random:
        trigger_in = 0
        cv_output = 1

    class Stompswitch:
        cv_output = 0

    class Value:
        value = 0
        cv_output = 1

    class CVDelay:
        cv_input = 0
        delay_time = 1
        cv_output = 2

    class Comparator:
        cv_positive_input = 0
        cv_negative_input = 1
        cv_output = 2

    class CVRectify:
        cv_input = 0
        cv_output = 1

    class Pixel:
        cv_in = 0

    class EuroCVOut:
        cv_in = 0

    class CVMixer:
        # cv_in_N = N-1 for N in 1..8
        # atten_N = N+7 for N in 1..8
        cv_output = 16

        @staticmethod
        def cv_in(n: int) -> int:
            """Block position for cv_in_N (1-indexed)."""
            return n - 1

        @staticmethod
        def atten(n: int) -> int:
            """Block position for atten_N (1-indexed)."""
            return n + 7


# ── ModuleIndex loading ───────────────────────────────────────────────────────

_SCHEMA_PATH = Path(__file__).parent / "schema" / "ModuleIndex.json"

def _load_index() -> dict:
    with open(_SCHEMA_PATH) as f:
        return json.load(f)

_INDEX: dict | None = None

def module_index() -> dict:
    global _INDEX
    if _INDEX is None:
        _INDEX = _load_index()
    return _INDEX


def mod_info(mod_idx: int) -> dict:
    return module_index()[str(mod_idx)]


def mod_params(mod_idx: int) -> int:
    return mod_info(mod_idx)["params"]


def mod_name(mod_idx: int) -> str:
    return mod_info(mod_idx)["name"]


def mod_cpu(mod_idx: int) -> float:
    return mod_info(mod_idx)["cpu"]


def module_size(mod_idx: int, saved_data_words: int = 0) -> int:
    """Total 4-byte word count for a module record."""
    return 14 + mod_params(mod_idx) + saved_data_words


# ── Color map ─────────────────────────────────────────────────────────────────

COLOR_ID = {
    "Blue": 1, "Green": 2, "Red": 3, "Yellow": 4, "Aqua": 5,
    "Magenta": 6, "White": 7, "Orange": 8, "Lima": 9, "Surf": 10,
    "Sky": 11, "Purple": 12, "Pink": 13, "Peach": 14, "Mango": 15,
}

# ── Module dict builder ───────────────────────────────────────────────────────

def make_module(
    number: int,
    mod_idx: int,
    page: int,
    position: int,
    options_binary: list[int],
    params_raw: list[int],
    color: str = "Blue",
    name: str = "",
    saved_data: list[int] | None = None,
    mod_version: int = 0,
) -> dict:
    """Build a module dict compatible with the zoia_lib encoder.

    position: first grid cell (0-39 per page).
    options_binary: up to 8 option index values; padded with zeros.
    params_raw: raw parameter values (0-65535); must have mod_params(mod_idx) entries.
    mod_version: the firmware module-type version (byte offset 28 in spec).
    """
    if saved_data is None:
        saved_data = []

    n_params = mod_params(mod_idx)
    saved_data_words = (len(saved_data) + 3) // 4

    # Ensure params_raw has the right length.
    raw = list(params_raw)
    if len(raw) < n_params:
        raw.extend([0] * (n_params - len(raw)))
    elif len(raw) > n_params:
        raw = raw[:n_params]

    # Pad options_binary to 8 bytes.
    opts = list(options_binary)
    if len(opts) < 8:
        opts.extend([0] * (8 - len(opts)))

    color_id = COLOR_ID.get(color, 1)
    n_blocks = mod_info(mod_idx)["min_blocks"]  # used for position list only

    return {
        "number": number,
        "mod_idx": mod_idx,
        "type": mod_name(mod_idx),
        "category": mod_info(mod_idx).get("category", ""),
        "cpu": mod_cpu(mod_idx),
        "name": name,
        "size": module_size(mod_idx, saved_data_words),
        "size_of_saveable_data": mod_version,
        "version": 0,
        "page": page,
        "position": [position + i for i in range(n_blocks)],
        "color": color,
        "header_color_id": color_id,
        "options": {},
        "options_binary": {str(i): v for i, v in enumerate(opts[:8])},
        "params": n_params,
        "parameters": {},
        "parameters_raw": raw,
        "saved_data": list(saved_data),
        "blocks": {},
        "connections": [],
        "starred": [],
    }


def make_connection(
    src_mod: int,
    src_block: int,
    dst_mod: int,
    dst_block: int,
    strength: int = 10000,
) -> dict:
    """Build a connection dict. strength: 0-10000 (100% = 0dB = 10000)."""
    return {
        "source": f"{src_mod}.{src_block}",
        "destination": f"{dst_mod}.{dst_block}",
        "strength": strength // 100,
        "source_raw": src_mod,
        "source_block_raw": src_block,
        "dest_raw": dst_mod,
        "dest_block_raw": dst_block,
        "strength_raw": strength,
    }
