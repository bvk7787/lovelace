"""Patch builder for the Euroburo paraphonic relationship sequencer.

Builds a patch dict suitable for encoder.encode().

Phase 1: V1 core — LFO clock, 16-step sequencer, scale quantizer, scale-aware
         mutation engine with seed control, CV Out 1.
Phase 2: V2 relationship engine — interval, phase, contrary motion, divergence.
Phase 3: Gate logic and Page 2 stompswitch controls.
Phase 4: Visual feedback (pixels).

Page layout:
  Page 0 — Performance (stompswitches, pixels)
  Page 1 — Relationship parameters (V2 controls)
  Page 2 — Processing bank A (clock, sequencer, S&H chain, random)
  Page 3 — Processing bank B (mutation math, comparators, switches)
  Page 4 — Processing bank C (V2 math, quantizers, outputs)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lovelace.modules import (
    B,
    COMPARATOR,
    CV_DELAY,
    CV_INVERT,
    CV_MIXER,
    CV_RECTIFY,
    EURO_CV_OUT_1,
    EURO_CV_OUT_2,
    EURO_CV_OUT_3,
    IN_SWITCH,
    LFO,
    MULTIPLIER,
    PIXEL,
    QUANTIZER,
    RANDOM,
    SAMPLE_AND_HOLD,
    SEQUENCER,
    STOMPSWITCH,
    VALUE,
    make_connection,
    make_module,
    mod_params,
)

# ── Configuration ─────────────────────────────────────────────────────────────

# LFO frequency range in ZOIA is approximately 0–100 Hz.
# param_0 at 0.0–1.0 (raw 0–65535) maps to this range.
_LFO_MAX_HZ = 100.0

# ZOIA pitch CV: 0–1 maps over the quantizer's output range.
# Step values at 0.5 put all steps at the middle of the pitch range.
_DEFAULT_STEP_VALUE = 32768  # 0.5 * 65535

# Mutation probability values (0.0–1.0)
_MUT_LO = 0.15
_MUT_MID = 0.40
_MUT_HI = 0.80

# Interval delta threshold: max allowed semitone jump for mutation to pass
# (expressed as fraction of the CV range; ~3 semitones ≈ 0.05 of a 5V/oct range)
_INTERVAL_THRESH = 0.10

# Interval offsets for V2 (semitones as fractions of full CV range, ~5 octaves)
# 1 octave = 1.0V on a 5V/oct system; 1 semitone ≈ 1/12 oct ≈ 0.0167 * (1/5) = 0.00333
# For ZOIA's normalised CV range (0–1 = 0–5V approx at 1V/oct): 1 oct = 0.2
_SEMITONE = 1.0 / 60.0  # ≈ 0.01667 (1 semitone in ZOIA's 5-octave CV space)

INTERVAL_OFFSETS = {
    "uni":  0.0,
    "3rd":  4 * _SEMITONE,
    "4th":  5 * _SEMITONE,
    "5th":  7 * _SEMITONE,
    "6th":  9 * _SEMITONE,
    "7th":  11 * _SEMITONE,
    "oct":  12 * _SEMITONE,
    "-oct": -12 * _SEMITONE,
}

# Divergence probabilities (0–1)
DIVERGE_PROBS = [0.0, 0.15, 0.30, 0.50, 0.65, 0.80, 0.90, 1.0]


@dataclass
class PatchConfig:
    name: str = "lovelace"
    bpm: float = 120.0
    # Quantizer: key 0–11 (C=0, C#=1, ..., B=11), scale 0=major/Ionian
    # In ZOIA "basic" mode the scale index maps to the scale selector.
    # Dorian is index 1 in most ZOIA firmware versions.
    key: int = 0
    scale: int = 1       # 0=major, 1=dorian, 2=phrygian, 3=lydian, 4=mixolydian,
                         # 5=aeolian/natural minor, 6=locrian, 7=chromatic (basic mode)
    phase: int = 1       # build phase: 1=V1 only, 2=+V2, 3=+gates+controls
    visuals: bool = False


# ── Grid packer ───────────────────────────────────────────────────────────────

class GridPacker:
    """Assigns grid positions to modules across pages, no overlaps."""

    CELLS_PER_PAGE = 40

    def __init__(self, start_page: int = 2):
        self._page = start_page
        self._pos = 0

    def assign(self, n_blocks: int) -> tuple[int, int]:
        """Return (page, position) for a module needing n_blocks cells."""
        if n_blocks > self.CELLS_PER_PAGE:
            raise ValueError(f"Module needs {n_blocks} cells but page only has {self.CELLS_PER_PAGE}")
        if self._pos + n_blocks > self.CELLS_PER_PAGE:
            self._page += 1
            self._pos = 0
        page = self._page
        pos = self._pos
        self._pos += n_blocks
        return page, pos


def _block_count(mod_idx: int, options_binary: list[int]) -> int:
    """Approximate visible block count for grid packing.

    Uses min_blocks from the module index as a lower bound and applies the
    most common option-dependent adjustments for the modules we actually use.
    Returns the number of grid cells consumed.
    """
    from lovelace.modules import mod_info
    info = mod_info(mod_idx)
    min_b = info["min_blocks"]

    if mod_idx == SEQUENCER:
        # options_binary[0] = number_of_steps (0-indexed; value N → N+1 steps)
        n_steps = options_binary[0] + 1
        n_tracks = options_binary[1] + 1
        restart = options_binary[2]      # 1 = on
        key_inp = options_binary[4]      # 0 = off
        n = n_steps + 1  # steps + gate_in
        if restart:
            n += 1
        if key_inp:
            n += 2
        n += n_tracks   # out_track per track
        return n
    if mod_idx == LFO:
        # cv_control + output (with our no-swing, no-phase settings)
        return 2
    if mod_idx == IN_SWITCH:
        n_inputs = options_binary[0] + 1
        return n_inputs + 2   # inputs + in_select + cv_output
    if mod_idx == MULTIPLIER:
        n_inputs = options_binary[0] + 2  # index 0 = 2 inputs, etc.
        return n_inputs + 1   # inputs + output
    if mod_idx == CV_MIXER:
        # Falls through to `else: blocks = d` in zoia_lib = all 17 blocks
        return 17
    if mod_idx == RANDOM:
        # new_val_on_trig at options_binary[1]
        return 2 if options_binary[1] else 1
    return min_b


# ── Patch builder ─────────────────────────────────────────────────────────────

def build_patch(cfg: PatchConfig | None = None) -> dict:
    """Assemble the full patch dict from a PatchConfig."""
    if cfg is None:
        cfg = PatchConfig()

    modules: list[dict] = []
    connections: list[dict] = []
    packer = GridPacker(start_page=2)

    def add_module(mod_idx: int, opts_bin: list[int], params: list[int],
                   color: str = "Blue", name: str = "") -> int:
        """Add a module; return its index in the modules list."""
        idx = len(modules)
        n_blocks = _block_count(mod_idx, opts_bin)
        page, pos = packer.assign(n_blocks)
        m = make_module(
            number=idx,
            mod_idx=mod_idx,
            page=page,
            position=pos,
            options_binary=opts_bin,
            params_raw=params,
            color=color,
            name=name,
        )
        modules.append(m)
        return idx

    def wire(src: int, src_b: int, dst: int, dst_b: int, strength: int = 10000):
        connections.append(make_connection(src, src_b, dst, dst_b, strength))

    # ── Clock frequency ───────────────────────────────────────────────────────
    # At BPM=120 with 16th-note steps: clock = 120/60 * 4 = 8 Hz
    # LFO param_0 (cv_control) normalised: value/65535 * _LFO_MAX_HZ = target_hz
    clock_hz = (cfg.bpm / 60.0) * 4.0   # 16th-note rate
    lfo_rate_raw = int(min(clock_hz / _LFO_MAX_HZ, 1.0) * 65535)

    # ── Phase 1: V1 core ──────────────────────────────────────────────────────

    # 0: LFO — square wave, 0-to-1 output, CV rate input
    #    opts: waveform=square(0), swing=off(0), output=0to1(0), input=cv(0),
    #          phase_input=off(0), phase_reset=off(0)
    i_lfo = add_module(LFO, [0, 0, 0, 0, 0, 0],
                       [lfo_rate_raw, 0, 0, 0], color="Sky", name="clock")

    # 1: Sequencer — 16 steps, 1 track, loop, no restart, no key input
    #    opts: num_steps=16(idx15), num_tracks=1(idx0), restart=off(0),
    #          behavior=loop(0), key_input=off(0), num_pages=1(0)
    seq_params = [_DEFAULT_STEP_VALUE] * 32 + [0, 0, 0, 0]
    i_seq = add_module(SEQUENCER, [15, 0, 0, 0, 0, 0],
                       seq_params, color="Green", name="seq")

    # 2: Random A — unipolar, triggered (mutation candidate source)
    #    opts: output=0to1(0), new_val_on_trig=on(1)
    i_rnd_a = add_module(RANDOM, [0, 1], [32768], color="Orange", name="rnd_a")

    # 3: S&H A — sample mutation candidate on each clock tick
    i_sh_a = add_module(SAMPLE_AND_HOLD, [0], [0, 0], color="Orange", name="sh_a")

    # 4: S&H B — capture previous step value (clocked simultaneously)
    i_sh_b = add_module(SAMPLE_AND_HOLD, [0], [0, 0], color="Yellow", name="sh_b")

    # 5: Random (probability source) — clocked, for mutation probability roll
    i_rnd_prob = add_module(RANDOM, [0, 1], [32768], color="Magenta", name="rnd_prob")

    # 6: Value lo — low mutation probability threshold
    lo_raw = int(_MUT_LO * 65535)
    i_val_lo = add_module(VALUE, [1], [lo_raw, 0], color="Lima", name="mut_lo")

    # 7: Value mid — mid mutation probability threshold
    mid_raw = int(_MUT_MID * 65535)
    i_val_mid = add_module(VALUE, [1], [mid_raw, 0], color="Lima", name="mut_mid")

    # 8: Value hi — high mutation probability threshold
    hi_raw = int(_MUT_HI * 65535)
    i_val_hi = add_module(VALUE, [1], [hi_raw, 0], color="Lima", name="mut_hi")

    # 9: Value interval threshold — max allowed delta for mutation to pass
    thresh_raw = int(_INTERVAL_THRESH * 65535)
    i_val_thresh = add_module(VALUE, [1], [thresh_raw, 0], color="Aqua", name="thresh")

    # 10: Stompswitch SEED — left stompswitch, momentary, normally zero
    #     When held: forces mutation rate to hi (rapid evolution)
    i_seed = add_module(STOMPSWITCH, [0, 0, 0], [], color="Red", name="seed")

    # 11: In Switch (4 inputs) — select mutation rate: lo / mid / hi / seed
    #     in_select driven by seed stompswitch (0=lo, 1=mid, 2=hi, 3=hi again via seed)
    #     We'll wire seed stompswitch to in_select so when pressed it jumps to input 3
    i_sw_rate = add_module(IN_SWITCH, [3], [0] * 17, color="Purple", name="rate_sw")

    # 12: CV Invert — negate S&H B output for delta subtraction
    i_cv_inv = add_module(CV_INVERT, [], [0], color="White", name="cv_inv")

    # 13: CV Mixer 2-channel — delta = S&H_A (candidate) + (-S&H_B) = candidate - prev
    #     Both channels at unity gain: atten_1 = atten_2 = 1.0 (65535)
    cv_mix_params = [0] * 8 + [65535, 65535] + [0] * 6
    i_cv_mix_delta = add_module(CV_MIXER, [1, 0], cv_mix_params, color="White", name="delta")

    # 14: CV Rectify — |delta| absolute value
    i_cv_rect = add_module(CV_RECTIFY, [], [0], color="White", name="abs_d")

    # 15: Comparator A — |delta| <= threshold? (interval constraint check)
    #     Output high when positive_input <= negative_input, i.e. |delta| <= thresh
    #     Actually Comparator fires when pos > neg. We want to fire when |delta| < thresh.
    #     Workaround: connect thresh to positive, |delta| to negative →
    #     fires when thresh > |delta|.
    i_comp_a = add_module(COMPARATOR, [0], [0, 0], color="Aqua", name="comp_a")

    # 16: Comparator B — mutation probability fires?
    #     Fires when random_prob > selected_rate
    i_comp_b = add_module(COMPARATOR, [0], [0, 0], color="Magenta", name="comp_b")

    # 17: Multiplier 2-input — AND gate: mutation allowed only when both comparators fire
    i_and = add_module(MULTIPLIER, [0], [0, 0], color="White", name="and")

    # 18: In Switch A (2 inputs) — pass original seq CV or mutation candidate
    #     in_select driven by AND gate output
    i_sw_a = add_module(IN_SWITCH, [1], [0] * 17, color="Green", name="sw_a")

    # 19: Quantizer A — snap to scale
    #     opts: key_scale_jacks=no(0), scales=basic(0)
    #     params: cv_input(0→connection), key(root note), scale(mode index)
    key_raw = int((cfg.key / 12.0) * 65535)
    scale_raw = int((cfg.scale / 7.0) * 65535)  # 8 scales in basic mode
    i_quant_a = add_module(QUANTIZER, [0, 0], [0, 0, key_raw, scale_raw],
                           color="Green", name="quant_a")

    # 20: Euro CV Out 1 — V1 pitch output
    i_cv_out_1 = add_module(EURO_CV_OUT_1, [0, 0, 0], [0], color="Blue", name="v1_out")

    # 21: Euro CV Out 3 — Gate output (LFO square wave = clock pulse as gate)
    i_cv_out_gate = add_module(EURO_CV_OUT_3, [0, 0, 0], [0], color="Blue", name="gate1")

    # ── Connections — Phase 1 ─────────────────────────────────────────────────

    # LFO → Sequencer clock
    wire(i_lfo, B.LFO.output, i_seq, B.Sequencer.gate_in)

    # LFO → S&H triggers (sample on each clock)
    wire(i_lfo, B.LFO.output, i_sh_a, B.SH.trigger)
    wire(i_lfo, B.LFO.output, i_sh_b, B.SH.trigger)
    wire(i_lfo, B.LFO.output, i_rnd_prob, B.Random.trigger_in)
    wire(i_lfo, B.LFO.output, i_rnd_a, B.Random.trigger_in)

    # Sequencer → S&H B (capture previous step value)
    wire(i_seq, B.Sequencer.out_track_1, i_sh_b, B.SH.cv_input)

    # Sequencer → In Switch A input 1 (original path)
    wire(i_seq, B.Sequencer.out_track_1, i_sw_a, 0)  # cv_input_1 = pos 0

    # Random A → S&H A (sample mutation candidate)
    wire(i_rnd_a, B.Random.cv_output, i_sh_a, B.SH.cv_input)

    # Delta calculation: candidate - previous
    wire(i_sh_a, B.SH.cv_output, i_cv_mix_delta, B.CVMixer.cv_in(1))
    wire(i_sh_b, B.SH.cv_output, i_cv_inv, B.CVInvert.cv_input)
    wire(i_cv_inv, B.CVInvert.cv_output, i_cv_mix_delta, B.CVMixer.cv_in(2))
    wire(i_cv_mix_delta, B.CVMixer.cv_output, i_cv_rect, B.CVRectify.cv_input)

    # Comparator A: threshold (positive) vs |delta| (negative) → fires when thresh > |delta|
    wire(i_val_thresh, B.Value.cv_output, i_comp_a, B.Comparator.cv_positive_input)
    wire(i_cv_rect, B.CVRectify.cv_output, i_comp_a, B.Comparator.cv_negative_input)

    # Mutation rate selection
    wire(i_val_lo, B.Value.cv_output, i_sw_rate, 0)    # input 1 = lo
    wire(i_val_mid, B.Value.cv_output, i_sw_rate, 1)   # input 2 = mid
    wire(i_val_hi, B.Value.cv_output, i_sw_rate, 2)    # input 3 = hi
    wire(i_val_hi, B.Value.cv_output, i_sw_rate, 3)    # input 4 = hi (seed → max)

    # Seed stompswitch scales the in_select: 0=lo, ~0.33=mid, ~0.67=hi, 1.0=hi(seed)
    # The seed switch drives in_select directly; when not pressed (=0): lo
    # When pressed (momentary = 1.0): maps to input 4 (index 3 = 1.0 on 0-1 range for 4 inputs)
    wire(i_seed, B.Stompswitch.cv_output, i_sw_rate, B.InSwitch.in_select)

    # Comparator B: random_prob (positive) vs selected_rate (negative)
    wire(i_rnd_prob, B.Random.cv_output, i_comp_b, B.Comparator.cv_positive_input)
    wire(i_sw_rate, B.InSwitch.cv_output, i_comp_b, B.Comparator.cv_negative_input)

    # AND gate: both comparators must fire for mutation
    wire(i_comp_a, B.Comparator.cv_output, i_and, B.Multiplier.cv_input_1)
    wire(i_comp_b, B.Comparator.cv_output, i_and, B.Multiplier.cv_input_2)

    # AND output drives In Switch A selection
    wire(i_and, B.Multiplier.cv_output, i_sw_a, B.InSwitch.in_select)

    # S&H A (mutation candidate) → In Switch A input 2
    wire(i_sh_a, B.SH.cv_output, i_sw_a, 1)  # cv_input_2 = pos 1

    # In Switch A → Quantizer A → CV Out 1
    wire(i_sw_a, B.InSwitch.cv_output, i_quant_a, B.Quantizer.cv_input)
    wire(i_quant_a, B.Quantizer.cv_output, i_cv_out_1, B.EuroCVOut.cv_in)

    # Gate out: LFO square wave → CV Out 3
    wire(i_lfo, B.LFO.output, i_cv_out_gate, B.EuroCVOut.cv_in)

    # ── Phase 2: V2 relationship engine ───────────────────────────────────────
    if cfg.phase >= 2:
        _add_v2(cfg, modules, connections, packer, add_module, wire,
                i_lfo, i_quant_a, i_seq)

    # ── Page metadata ─────────────────────────────────────────────────────────
    n_processing_pages = packer._page - 2 + 1
    n_pages = 2 + n_processing_pages   # page 0 + page 1 + processing pages
    pages = ["perform", "relate"] + [""] * n_processing_pages

    # Patch metadata
    total_cpu = sum(m["cpu"] for m in modules)
    patch = {
        "name": cfg.name[:16],
        "size": 0,  # computed by encoder
        "modules": modules,
        "connections": connections,
        "pages": pages,
        "pages_count": n_pages,
        "starred": [],
        "colors": [],
        "meta": {
            "name": cfg.name,
            "cpu": round(total_cpu, 2),
            "n_modules": len(modules),
            "n_connections": len(connections),
            "n_pages": n_pages,
            "n_starred": 0,
        },
    }
    return patch


# ── Phase 2: V2 relationship engine ──────────────────────────────────────────

def _add_v2(cfg, modules, connections, packer, add_module, wire,
            i_lfo, i_quant_a, i_seq):
    """Add V2 modules and connections to an in-progress patch build."""

    def _wire(src, src_b, dst, dst_b, strength=10000):
        connections.append(make_connection(src, src_b, dst, dst_b, strength))

    # ── Interval path ─────────────────────────────────────────────────────────
    # V1 post-quantize + interval_offset → Quantizer B → V2 interval pitch

    # Value for interval offset (default: 5th = 7 semitones)
    interval_frac = INTERVAL_OFFSETS["5th"]
    # We need to add a constant to the quantizer output. Use CV Mixer 2ch.
    # ch1 = V1 quantizer output (unity), ch2 = Value (interval offset)
    interval_val_raw = int(abs(interval_frac) * 65535)
    i_val_interval = add_module(VALUE, [1], [interval_val_raw, 0],
                                color="Mango", name="interval")

    cv_mix_params_interval = [0] * 8 + [65535, 65535] + [0] * 6
    i_cv_mix_interval = add_module(CV_MIXER, [1, 0], cv_mix_params_interval,
                                   color="Mango", name="v2_int_mix")

    key_raw = int((cfg.key / 12.0) * 65535)
    scale_raw = int((cfg.scale / 7.0) * 65535)
    i_quant_b = add_module(QUANTIZER, [0, 0], [0, 0, key_raw, scale_raw],
                           color="Mango", name="quant_b")

    # ── Phase offset path ─────────────────────────────────────────────────────
    # S&H chain: each captures the previous S&H output on each clock tick.
    # Tap at 4, 8, 12 steps behind V1.

    i_sh_p4 = add_module(SAMPLE_AND_HOLD, [0], [0, 0], color="Surf", name="sh_p4")
    i_sh_p8 = add_module(SAMPLE_AND_HOLD, [0], [0, 0], color="Surf", name="sh_p8")
    i_sh_p12 = add_module(SAMPLE_AND_HOLD, [0], [0, 0], color="Surf", name="sh_p12")

    # In Switch C: phase tap selection (4 inputs: V1, 4-step, 8-step, 12-step)
    i_sw_phase = add_module(IN_SWITCH, [3], [0] * 17, color="Surf", name="phase_sw")

    # ── Contrary motion path ──────────────────────────────────────────────────
    # Track V1 and V2 previous values; apply negated V1 delta to V2.

    i_sh_v1_prev = add_module(SAMPLE_AND_HOLD, [0], [0, 0], color="Sky", name="v1_prv")
    i_sh_v2_prev = add_module(SAMPLE_AND_HOLD, [0], [0, 0], color="Sky", name="v2_prv")

    # Math C: V1_delta = V1_current - V1_prev
    i_inv_v1p = add_module(CV_INVERT, [], [0], color="Sky", name="inv_v1p")
    cv_mix_c_params = [0] * 8 + [65535, 65535] + [0] * 6
    i_cv_mix_c = add_module(CV_MIXER, [1, 0], cv_mix_c_params, color="Sky", name="v1_dlt")

    # Math D: negate V1 delta
    i_inv_delta = add_module(CV_INVERT, [], [0], color="Sky", name="inv_dlt")

    # Math E: V2_contrary = V2_prev + (-V1_delta)
    cv_mix_e_params = [0] * 8 + [65535, 65535] + [0] * 6
    i_cv_mix_e = add_module(CV_MIXER, [1, 0], cv_mix_e_params, color="Sky", name="v2_ctr")

    # ── Divergence path ───────────────────────────────────────────────────────

    i_rnd_divg = add_module(RANDOM, [0, 1], [32768], color="Pink", name="rnd_div")
    i_sh_divg = add_module(SAMPLE_AND_HOLD, [0], [0, 0], color="Pink", name="sh_div")
    i_quant_c = add_module(QUANTIZER, [0, 0], [0, 0, key_raw, scale_raw],
                           color="Pink", name="quant_c")

    divg_thresh_raw = int(0.5 * 65535)  # 50% divergence default
    i_val_divg = add_module(VALUE, [1], [divg_thresh_raw, 0], color="Pink", name="divg_thr")
    i_rnd_divg_gate = add_module(RANDOM, [0, 1], [32768], color="Pink", name="rnd_dg")
    i_comp_divg = add_module(COMPARATOR, [0], [0, 0], color="Pink", name="cmp_div")

    # ── V2 routing ────────────────────────────────────────────────────────────
    # Switch E (motion type): 3 inputs — interval, contrary, diverge
    # Default: parallel (input 1 = interval)
    i_sw_motion = add_module(IN_SWITCH, [2], [0] * 17, color="Peach", name="motion_sw")

    # Switch F (diverge gate): 2 inputs — derived pitch, random pitch
    # in_select driven by Comparator C (diverge probability)
    i_sw_divg_gate = add_module(IN_SWITCH, [1], [0] * 17, color="Pink", name="divg_sw")

    # V2 output
    i_cv_out_2 = add_module(EURO_CV_OUT_2, [0, 0, 0], [0], color="Blue", name="v2_out")

    # ── V2 Connections ────────────────────────────────────────────────────────

    # Interval path
    _wire(i_quant_a, B.Quantizer.cv_output, i_cv_mix_interval, B.CVMixer.cv_in(1))
    _wire(i_val_interval, B.Value.cv_output, i_cv_mix_interval, B.CVMixer.cv_in(2))
    _wire(i_cv_mix_interval, B.CVMixer.cv_output, i_quant_b, B.Quantizer.cv_input)

    # Phase offset chain (each S&H captures previous S&H on clock)
    _wire(i_quant_a, B.Quantizer.cv_output, i_sh_p4, B.SH.cv_input)
    _wire(i_sh_p4, B.SH.cv_output, i_sh_p8, B.SH.cv_input)
    _wire(i_sh_p8, B.SH.cv_output, i_sh_p12, B.SH.cv_input)
    _wire(i_lfo, B.LFO.output, i_sh_p4, B.SH.trigger)
    _wire(i_lfo, B.LFO.output, i_sh_p8, B.SH.trigger)
    _wire(i_lfo, B.LFO.output, i_sh_p12, B.SH.trigger)

    # Phase switch inputs: V1, 4-step, 8-step, 12-step behind
    _wire(i_quant_a, B.Quantizer.cv_output, i_sw_phase, 0)  # input 1 = 0 phase
    _wire(i_sh_p4, B.SH.cv_output, i_sw_phase, 1)           # input 2 = 4 steps
    _wire(i_sh_p8, B.SH.cv_output, i_sw_phase, 2)           # input 3 = 8 steps
    _wire(i_sh_p12, B.SH.cv_output, i_sw_phase, 3)          # input 4 = 12 steps

    # Contrary motion: track V1 and V2 previous values
    _wire(i_quant_a, B.Quantizer.cv_output, i_sh_v1_prev, B.SH.cv_input)
    _wire(i_lfo, B.LFO.output, i_sh_v1_prev, B.SH.trigger)

    # Math C: V1 delta
    _wire(i_quant_a, B.Quantizer.cv_output, i_cv_mix_c, B.CVMixer.cv_in(1))
    _wire(i_sh_v1_prev, B.SH.cv_output, i_inv_v1p, B.CVInvert.cv_input)
    _wire(i_inv_v1p, B.CVInvert.cv_output, i_cv_mix_c, B.CVMixer.cv_in(2))

    # Math D + E: V2 contrary pitch
    _wire(i_cv_mix_c, B.CVMixer.cv_output, i_inv_delta, B.CVInvert.cv_input)
    _wire(i_sh_v2_prev, B.SH.cv_output, i_cv_mix_e, B.CVMixer.cv_in(1))
    _wire(i_inv_delta, B.CVInvert.cv_output, i_cv_mix_e, B.CVMixer.cv_in(2))

    # Divergence path
    _wire(i_rnd_divg, B.Random.cv_output, i_sh_divg, B.SH.cv_input)
    _wire(i_lfo, B.LFO.output, i_sh_divg, B.SH.trigger)
    _wire(i_lfo, B.LFO.output, i_rnd_divg, B.Random.trigger_in)
    _wire(i_sh_divg, B.SH.cv_output, i_quant_c, B.Quantizer.cv_input)

    # Diverge gate comparator: fires when random > threshold
    _wire(i_rnd_divg_gate, B.Random.cv_output, i_comp_divg, B.Comparator.cv_positive_input)
    _wire(i_val_divg, B.Value.cv_output, i_comp_divg, B.Comparator.cv_negative_input)
    _wire(i_lfo, B.LFO.output, i_rnd_divg_gate, B.Random.trigger_in)

    # Motion type switch: input 1=interval, 2=contrary, 3=diverge
    _wire(i_quant_b, B.Quantizer.cv_output, i_sw_motion, 0)   # parallel (interval)
    _wire(i_cv_mix_e, B.CVMixer.cv_output, i_sw_motion, 1)    # contrary
    _wire(i_quant_c, B.Quantizer.cv_output, i_sw_motion, 2)   # diverge (quantized random)

    # Diverge switch: input 1=derived(motion output), 2=raw random
    _wire(i_sw_motion, B.InSwitch.cv_output, i_sw_divg_gate, 0)
    _wire(i_quant_c, B.Quantizer.cv_output, i_sw_divg_gate, 1)
    _wire(i_comp_divg, B.Comparator.cv_output, i_sw_divg_gate, B.InSwitch.in_select)

    # V2 output → CV Out 2
    _wire(i_sw_divg_gate, B.InSwitch.cv_output, i_cv_out_2, B.EuroCVOut.cv_in)

    # V2 prev tracking (feedback from V2 output before CV out)
    _wire(i_sw_divg_gate, B.InSwitch.cv_output, i_sh_v2_prev, B.SH.cv_input)
    _wire(i_lfo, B.LFO.output, i_sh_v2_prev, B.SH.trigger)
