# CLAUDE.md — Euroburo Paraphonic Relationship Sequencer

## Project Goal

Build a Python tool that generates a valid `.zoia` binary patch file implementing a
two-voice generative sequencer for the Empress Effects ZOIA Euroburo. The patch
is designed specifically for use with an Intellijel Cascadia semi-modular synthesizer,
exploiting Cascadia's two oscillators to produce paraphonic counterpoint.

---

## Hardware Context

**Euroburo** — Eurorack version of the Empress Effects ZOIA. A programmable modular
environment with a 5x8 RGB button grid, encoder, OLED screen, 4 CV inputs, 4 CV
outputs, stereo audio I/O, and MIDI I/O. Patches are stored as binary `.zoia` files
on an SD card. Up to 64 pages of grid per patch. No hard module count limit —
the constraint is DSP/CPU, with a visible CPU meter on the OLED.

**Cascadia** — Intellijel semi-modular synthesizer with two oscillators (VCO A, VCO B),
shared filter and VCA, multiple envelopes and LFOs, slider-based expression controls.
Key characteristic: feeding VCO A and VCO B separate pitch CVs produces paraphonic
behavior — two independent pitches sharing one filter and VCA. This creates counterpoint
with a blended timbre.

**Physical connection:** Euroburo CV Out 1 → Cascadia VCO A pitch CV.
Euroburo CV Out 2 → Cascadia VCO B pitch CV.
Euroburo Gate Out 1 → Cascadia envelope gate (Voice 1).
Euroburo Gate Out 2 → Cascadia envelope gate (Voice 2, if available).

---

## Musical Design Intent

This is not a standard step sequencer. It is a **two-voice relationship engine**.

Voice 1 is a 16-step generative sequence with scale-aware mutation — a smarter Turing
Machine. Voice 2 is derived from Voice 1 in real time via a set of configurable
relationship parameters. The musical interest comes from navigating that relationship
space live, not from programming two independent melodies.

**Influences shaping the design:**
- Trickfinger (John Frusciante): bassline-first, loop-based, builds from a groove
- Venetian Snares: sequencer-of-sequencers, rhythmic complexity
- Dorian Concept: harmonic sophistication, timbre as harmony

**Core design principles:**
- Randomness operates within a musical grammar (scale-aware, interval-constrained)
- Voice 2 is always mathematically related to Voice 1, not independently random
- The patch should feel like a performance instrument, not a programming interface
- CPU budget: CV utility modules only — no audio effects in this patch. Reverb/delay
  live in a separate patch. A pure CV sequencer patch should use well under 50% CPU.

---

## Patch Architecture

### Signal Flow

```
[CLOCK]
  LFO → master clock → sequencer advance, S&H triggers, gate timing

[VOICE 1 — GENERATIVE SEQUENCER]
  Sequencer (16 step) → raw CV
        |
        ├── S&H B captures previous step value
        |
  Random A → S&H A (clocked) → mutation candidate
        |
  Math A: |candidate − previous| = interval delta
        |
  Comparator A: delta within allowed interval range?
        |── gate
  Comparator B: mutation probability fires? (rate: lo/mid/hi)
        |
  Switch A: pass original CV or substitute mutation candidate
        |
  Quantizer A → V1 pitch → CV Out 1 (Cascadia VCO A)

  Seed stompswitch → momentarily forces mutation probability to max
  (sequence rapidly evolves to new state, then settles)

[VOICE 2 — RELATIONSHIP ENGINE]
  V1 pitch (post-quantize) feeds four parallel paths:

  INTERVAL path:
    V1 → Math B (add interval offset) → Quantizer B → interval pitch

  PHASE path:
    V1 → S&H C (4-step tap)
       → S&H D (8-step tap)
       → S&H E (12-step tap)
       → Switch C (phase selection: 0 / 4 / 8 / 12 steps behind)

  CONTRARY path:
    S&H F (prev V1 value) + S&H G (prev V2 value)
    → Math C: V1 current − V1 previous = delta
    → Math D: negate delta
    → Math E: V2 previous + negated delta = new V2 contrary pitch

  DIVERGE path:
    Random B → S&H H (clocked) → Quantizer C → free random pitch

  All four paths feed:
    Switch E (motion type: parallel / contrary / diverge)
       → Switch F (divergence gate: Comparator C fires at probability threshold)
       → CV Out 2 (Cascadia VCO B)

[GATES]
  Sequencer gate → Gate Out 1 (Voice 1)
  Gate Out 1 → Switch G (rhythm mode) → Gate Out 2 (Voice 2)
  Rhythm options: unison / offset (gate delay) / divided (÷2) / divided (÷4)

[PAGE 1 — PERFORMANCE CONTROLS]
  Row 1–2:  16 pixel indicators showing current step position
  Row 3:    [ SEED ] [ mut:lo ] [ mut:mid ] [ mut:hi ] [ ......  ] [ ...... ]
  Row 4–5:  available for macros or future extension

[PAGE 2 — RELATIONSHIP PARAMETERS]
  Row 1 (Interval):   [ uni  ] [ 3rd  ] [ 4th  ] [ 5th  ] [ 6th  ] [ 7th  ] [ oct ] [-oct ]
  Row 2 (Phase):      [  0   ] [  2   ] [  4   ] [  6   ] [  8   ] [ 10   ] [ 12  ] [ 14  ]
  Row 3 (Motion):     [paral ] [contr ] [divge ] [      ] [      ] [      ] [     ] [     ]
  Row 4 (Divergence): [  0%  ] [ 15%  ] [ 30%  ] [ 50%  ] [ 65%  ] [ 80%  ] [ 90% ] [100% ]
  Row 5 (Rhythm):     [unis  ] [offst ] [  ÷2  ] [  ÷4  ] [      ] [      ] [     ] [     ]

  One button lit per row at a time. Pressing a button on a row
  deselects the previous choice on that row and updates CV routing immediately.
```

### Module Inventory

| Section | Modules | Count |
|---|---|---|
| Clock | LFO | 1 |
| Voice 1 core | Sequencer (16-step), Quantizer A, Random A, S&H A, S&H B | 5 |
| Mutation logic | Math A, Comparator A, Comparator B, Switch A | 4 |
| Seed + rate control | Value A/B/C (lo/mid/hi probability), Switch B, Stompswitch (seed) | 5 |
| V2 Interval | Math B, Quantizer B, Value D (interval amount) | 3 |
| V2 Phase offset | S&H C/D/E (4/8/12-step taps), Switch C | 4 |
| V2 Contrary motion | S&H F, S&H G, Math C, Math D, Math E | 5 |
| V2 Divergence | Random B, S&H H, Quantizer C, Comparator C | 4 |
| V2 Routing | Switch D (phase tap select), Switch E (motion type), Switch F (diverge gate), Value E (diverge threshold) | 4 |
| Gates | Clock Divider, Gate Delay, Switch G | 3 |
| Stompswitches | Interval x6, Phase x4, Motion x3, Divergence x4, Rhythm x4, Mutation rate x3 | ~24 |
| Visual (optional) | Pixel x16 (step position), Pixel x5 (row state) | 21 |
| **Total (with visuals)** | | ~83 |
| **Total (no visuals)** | | ~62 |
| **Total (no visuals, no contrary)** | | ~57 |

**Priority build order:** Core V1 + mutation → V2 interval + phase → V2 contrary →
divergence → rhythm → visual feedback (last, optional).

### Relationship Parameter Behavior

**Interval (Row 1):** Adds a semitone offset to V1 CV before quantizing to scale.
Unison = 0. 3rd ≈ +4 semitones. 4th = +5. 5th = +7. 6th = +9. 7th = +11.
Oct = +12. -Oct = -12. The quantizer snaps the result to the active scale,
so interval relationships are harmonically intelligent, not mechanical transposition.

**Phase (Row 2):** V2 plays what V1 played N steps ago via S&H shift register taps.
At offset 0: unison. At offset 8: exactly half a cycle behind (call-and-response).
At offset 1-3: tight canon, voices nearly overlapping. Interaction with mutation:
in phase mode, V2 always echoes a past version of V1, so as V1 mutates V2 plays
its recent history — temporal smear effect.

**Motion (Row 3):**
- Parallel: V2 = interval path output (fixed interval above/below V1)
- Contrary: V2 moves opposite to V1's melodic direction (computed delta path)
- Diverge: V2 = derived pitch except when divergence probability fires (free random pitch)

**Divergence (Row 4):** Probability that any given step uses the free random pitch
instead of the relationship-derived pitch. At 0% V2 is fully determined. At 100%
V2 is fully random. In between: stochastic counterpoint.

**Rhythm (Row 5):** Gate pattern for Voice 2 relative to Voice 1.
Unison: same gate. Offset: gate delayed by half a step. ÷2: Voice 2 gates every
other step. ÷4: Voice 2 gates every fourth step.

---

## Technical Resources

| Resource | Location | Purpose |
|---|---|---|
| zoia_lib (Python) | `https://github.com/meanmedianmoge/zoia_lib` | Primary reference for binary format read/write. GPL 3.0. |
| Patch format spec | `zoia_lib/documentation/PatchFormat.pdf` | Binary format field-level documentation |
| Module index | `https://www.empresseffects.com/ZOIA-module-index` | Authoritative list of module names, parameters, ranges |
| Patchstorage | `https://patchstorage.com/platform/ZOIA` | Community patches for testing round-trip fidelity |
| Firmware changelog | `https://empresseffects.com/zoia-changelog` | Current firmware is 5.0 (Dec 2024). Relevant: sequencer module data size reduced in 5.0, 24% CPU optimization in 2.0 |

**Note on zoia_lib write support:** A contributor (sranderley) added binary encoding
capability. Assess completeness of the write path early. If gaps exist, build the
missing encode path from the PatchFormat.pdf spec rather than working around it.

---

## Development Approach

### Phase 0 — Validate round-trip (do this first)
1. Clone zoia_lib
2. Read 3-5 existing patches from patchstorage, dump to JSON
3. Understand the data structure at field level before designing against assumptions
4. Write a minimal patch from scratch: LFO → Sequencer → Quantizer → CV Out 1
5. Load on Euroburo and confirm it behaves correctly
6. If write path has gaps, patch zoia_lib encode or write a standalone encoder from PatchFormat.pdf

### Phase 1 — Voice 1 core
- 16-step sequencer with LFO clock
- Quantizer with configurable scale
- Random + S&H mutation with interval constraint
- Seed button (forces high mutation probability momentarily)
- CV Out 1 confirmed working on hardware

### Phase 2 — Voice 2 relationship engine
- Interval path (Math + Quantizer)
- Phase offset path (S&H shift register, 3 taps)
- Motion type switch (parallel first, then contrary, then diverge)
- CV Out 2 confirmed working on hardware

### Phase 3 — Gate logic and controls
- Gate Out 1 and Gate Out 2 with rhythm mode switch
- Page 2 stompswitch controls for all relationship parameters
- Mutation rate stompswitches on Page 1

### Phase 4 — Visual feedback (optional)
- 16-pixel step position indicator on Page 1
- Per-row state indicators on Page 2
- Only build this if CPU budget permits after Phase 3

---

## Key Technical Notes

- **CPU:** No hard module count limit. Constraint is DSP/CPU shown on OLED meter.
  Pure CV utility patches (no audio effects) run well under 50% CPU. Reverb, delay,
  and granular are the expensive modules — none are in this patch.
- **Pages:** Up to 64 pages per patch. This design uses 2 pages minimum.
- **Firmware:** Targeting firmware 5.0+ (December 2024). Sequencer module footprint
  was reduced in this version.
- **Scale quantization:** The quantizer snaps to a user-configured scale. The scale
  root and mode should be configurable parameters, not hardcoded.
- **Contrary motion math:** This requires tracking previous values of both V1 and V2
  using S&H modules clocked on each sequencer step. The delta computation
  (current − previous → negate → add to V2 previous) requires 3 Math modules and
  2 S&H modules. It is the most complex section of the patch.
- **Phase offset:** Implemented as a shift register — a chain of S&H modules each
  capturing the previous module's output on each clock tick. Three taps (4, 8, 12
  steps behind) gives meaningful phase variety without excessive module count.
  Offset 0 bypasses the chain entirely.
- **Seed button:** Does not rewrite sequencer step values directly (ZOIA sequencer
  module step values are set manually via encoder). Instead, the seed button
  momentarily sets mutation probability to maximum, causing the sequence to evolve
  rapidly to a new state over several steps, then probability returns to the
  user-selected rate.

---

## Out of Scope for This Patch

- Audio effects (reverb, delay, chorus) — these live in a separate effects patch
- Tempo sync to external MIDI clock — can be added in Phase 3 if desired
- Polyphony beyond two voices — Cascadia's shared filter limits this anyway
- Step-level lock controls — deferred; section-level locks could be a Phase 4 addition
