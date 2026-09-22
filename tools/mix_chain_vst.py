"""
mix_chain_vst — the original voice chain and master buss, loaded as commercial VST3 plug-ins.

NOT an entry point: a library imported by mix_music.py when `--engine vst`.
Deps come from mix_music.py's uv environment; see audio_dsp.py for why the sibling import works.

This is the chain mix_chain_oss.py was derived FROM, kept because a derivation is not the
thing itself: soothe2 tracks a resonance across a hundred bands as it moves, Pro-R 2 has early
reflections and tail modulation, Satin has real wow/flutter and companding. Where the two
engines disagree, this one is the reference. The cost is ~$1500 of licences installed at
/Library/Audio/Plug-Ins/VST3 before a render can run at all, and a much slower render (Satin
alone measured ~63% of mix wall-clock) — which is why `--engine oss` is the default.

The chain, in order: iZotope RX 12 De-plosive (Podcast Plosives, cleans low-end plosives
first) → FabFilter Pro-DS (Single Vocal / Female Wide Band, 7-14 kHz sibilance) → a second
Pro-DS tuned to hard "t"/"ts" transients (Wide Band, 3.5-7 kHz) → Pro-C 3 (Op-El, 32x
oversampling) → oeksound soothe2 (Vocal de-harsh 1) → Pro-R 2 (Vocal Rich Space), then u-he
Satin (SE Master Buss -18dB RMS) over the summed mix. soothe2 sits last before the reverb on
purpose: it works on an already-levelled signal, and the reverb is then fed a de-harshed voice.
Pro-DS / Pro-C 3 / Pro-R 2 are required; soothe2 and Satin are required unless bypassed with
--no-deharsh / --no-tape; RX De-plosive is optional and skipped when absent.

The factory presets are reconstructed by setting named VST3 parameters, each value verified
against the plug-in's own readout — pedalboard cannot load FabFilter .ffp / iZotope .xml /
u-he .h2p preset files. Those verifications (EXPECT, below) run inside load_vst on every
render and are NOT skipped by --no-self-test: they are inseparable from loading the plug-in,
and there is nothing to measure before one is loaded. --no-self-test only skips audio_dsp's
primitives suite, which covers the duck this engine shares with the other one.
"""

from __future__ import annotations
import math
import sys
from pathlib import Path

import numpy as np
from pedalboard import load_plugin

import audio_dsp
from audio_dsp import EPS, SR, db_to_lin

NAME = "vst"

# --- VST3 voice chain (macOS default plug-in folder) -------------------------
# The whole chain is iZotope + FabFilter plugins — no built-in/pedalboard DSP. pedalboard
# can't load FabFilter .ffp / iZotope .xml preset files, and its VST3 state injection is
# inert on them, so every factory preset is reconstructed by setting named parameters to
# values read from the preset file (or the plugin's own string_value readout).
VST3_DIR = Path("/Library/Audio/Plug-Ins/VST3")
PRO_DS = VST3_DIR / "FabFilter Pro-DS.vst3"
PRO_C3 = VST3_DIR / "FabFilter Pro-C 3.vst3"
PRO_R2 = VST3_DIR / "FabFilter Pro-R 2.vst3"
SOOTHE2 = VST3_DIR / "soothe2.vst3"
SATIN = VST3_DIR / "Satin.vst3"
RX_DEPLOSIVE = VST3_DIR / "RX 12 De-plosive.vst3"

# iZotope RX 12 De-plosive · "Podcast Plosives" factory preset (Podcast Plosives.xml).
# raw_value is normalized: sensitivity/strength on 0–10, frequency_limit on 50–500 Hz.
# Verified offline — a synthetic 90 Hz "p"-pop drops ~12 dB, the vowel stays intact.
RX_PODCAST_PLOSIVES = {
    "sensitivity": 5.72649574 / 10.0,          # 5.73
    "strength": 4.70085478 / 10.0,             # 4.70
    "frequency_limit_hz": (250.0 - 50.0) / 450.0,  # 250 Hz upper repair frequency
}

# Pro-DS · Single Vocal / Female Wide Band (.ffp: normalized threshold/HP/LP; real
# range 6 dB and lookahead 12 ms normalized by their param ranges 0–24 / 0–15).
PRO_DS_FEMALE_WIDE_BAND = {
    "mode": 0.0,                    # single de-esser
    "threshold": 0.4,               # normalized (== plugin default)
    "range": 6.0 / 24.0,            # 6 dB max reduction
    "band_processing": 0.0,         # Wide Band
    "stereo_link": 0.5,
    "lookahead": 12.0 / 15.0,       # 12 ms
    "lookahead_enabled": 1.0,
    "high_pass_frequency": 0.5441,  # 7.0 kHz — sibilance ("s"/"sh") detection HP
    "low_pass_frequency": 0.8451,   # 14 kHz
}

# Pro-DS · second instance, canonical two-stage de-essing to tame hard "t"/"ts" transients
# ("de-plosive"). The transient burst sits ~3.5–7 kHz — below the sibilance stage — so we
# move the detection band down and keep Wide Band so the whole signal dips briefly on the
# hit, softening the plosive punch. Verified on the real voice: ~3.9 dB reduction on the
# harsh-consonant hits, vowels left untouched (~0.01 dB). Threshold catches only loud t's.
PRO_DS_T_TRANSIENT = {
    "mode": 0.0,                    # Single Vocal
    "threshold": 0.40,              # -36 dB — engage on more of the t's
    "range": 0.40,                  # 9.6 dB max reduction (~-6.3 dB on the hits; vowels untouched)
    "band_processing": 0.0,         # Wide Band — softens the transient's punch
    "stereo_link": 0.5,
    "lookahead": 0.8,               # 12 ms — catch the fast onset before it passes
    "lookahead_enabled": 1.0,
    "high_pass_frequency": 0.25,    # 3.56 kHz — "t"/"ts" burst core, above vowel formants
    "low_pass_frequency": 0.5441,   # 7.0 kHz — meets the sibilance stage's low edge
}

# Pro-C 3 · Basic / Op-El (Optical-Electro) — text .ffp reconstructed and verified against
# string_value: style Op-El, -14 dB, 3.50:1, +20 dB soft knee, +60 dB range (uncapped),
# 3.8 ms attack, 180 ms release, auto-gain on. Smooth program-dependent optical leveling;
# the music sidechain duck (keyed off this compressed voice) stays intact (~-15 dB, verified).
# Oversampling forced to 32x for the cleanest transient handling.
PRO_C3_OPEL = {
    "style": 6.0 / 13.0,            # "Op-El" (14 styles, 0-based index 6)
    "threshold": (-14.0 + 60.0) / 60.0,  # -14 dB on the -60..0 range
    "ratio": 0.559666693210602,     # 3.50:1 (stored as raw)
    "knee": 20.0 / 72.0,            # +20 dB
    "range": 60.0 / 60.0,           # +60 dB (uncapped)
    "attack": 0.247060522437096,    # 3.8 ms
    "release": 0.380609452724457,   # 180 ms
    "auto_gain": 1.0,
    "auto_release": 0.0,
    "oversampling": 1.0,            # 32x (Off/2x/4x/8x/16x/32x)
}

# soothe2 · Vocals / "Vocal de-harsh 1" (Luis Barrera Jr., plugin preset v1.2.2). Dynamic
# resonance suppression, placed after Pro-C 3 so it works on a levelled signal and before
# Pro-R 2 so the reverb tail is fed an already-de-harshed voice (harshness sent to a reverb
# comes back as harshness). Unlike the FabFilter presets, soothe2's .preset file is plain XML
# in real units, so every value below is a direct read of the preset — converted to normalized
# raw with the plugin's own curves (freq: log 20 Hz–20 kHz; sens: linear ±12 dB; band Q: log
# 0.1–10; depth: linear ±18 dB; sharpness/selectivity/attack/release: linear 0–10) and each
# verified against string_value. Four dynamic bells — 924 Hz (+10.0), 2k46 (+10.8), 5k70
# (+7.2), 360 Hz (+2.9) — inside a 249 Hz–7k50 band, depth +4.1, 49.6% wet.
# The preset's XML band0/band5 carry no "sens" key: those are the low-cut/high-cut edges.
SOOTHE2_VOCAL_DEHARSH_1 = {
    "mode": 0.24875,                       # soft
    "depth": 0.6127647823757596,           # +4.06 dB
    "sharpness": 0.6023869037628173,       # 6.02
    "selectivity": 0.5096688747406006,     # 5.10
    "attack": 0.1,                         # 1.0
    "release": 0.0,                        # fast
    "low_cut_on": 0.75, "low_cut_freq_hz": 0.365237806687202,      # 249.3 Hz
    "low_cut_q": 0.003120045136198807,     # 0.707 — Butterworth, just off the 0.7 floor
    "low_cut_slope": 0.66625,              # 24 dB/oct
    "low_cut_balance": 0.5,                # even
    "band1_on": 0.75, "band1_freq_hz": 0.5549477812208038,         # 924.4 Hz
    "band1_sens_db": 0.9160676797231039,   # +9.99 dB
    "band1_q": 0.5534151204845793,         # 1.279
    "band1_mode": 0.19875, "band1_balance": 0.5,                   # bell, even
    "band2_on": 0.75, "band2_freq_hz": 0.6968382315213247,         # 2463.5 Hz
    "band2_sens_db": 0.94814666112264,     # +10.76 dB
    "band2_q": 0.5939130957612119,         # 1.541
    "band2_mode": 0.19875, "band2_balance": 0.5,
    "band3_on": 0.75, "band3_freq_hz": 0.8183056760293637,         # 5700.9 Hz
    "band3_sens_db": 0.799058755238851,    # +7.18 dB
    "band3_q": 0.4999998446841345,         # 1.000
    "band3_mode": 0.19875, "band3_balance": 0.5,
    "band4_on": 0.75, "band4_freq_hz": 0.41823121199180574,        # 359.5 Hz
    "band4_sens_db": 0.620781660079956,    # +2.90 dB
    "band4_q": 0.5,                        # 1.000
    "band4_mode": 0.19875, "band4_balance": 0.5,
    "high_cut_on": 0.75, "high_cut_freq_hz": 0.8580698337058124,   # 7503.1 Hz
    "high_cut_q": 0.003120045136198807,    # 0.707
    "high_cut_slope": 0.66625,             # 24 dB/oct
    "high_cut_balance": 0.5,
    "stereo_mode": 0.24875, "stereo_link": 1.0, "stereo_balance": 0.5,  # L|R, 100% link, even
    "oversample": 0.49875,                 # 2x
    "resolution": 0.66625,                 # high
    "offline_oversample": 0.9175,          # same as real-time
    "offline_resolution": 0.9375,          # same as real-time
    "mix": 0.4957360458374023,             # 49.6% wet
    "trim_db": 0.054399980439080134,       # +0.98 dB make-up
    "input_trim_db": 0.5,                  # 0 dB
    "delta": 0.24875, "bypass": 0.24875,                           # off
    "sidechain": 0.24875, "sidechain_solo_on": 0.24875,            # off
}

# Pro-R 2 · _2 Small / Vocal Rich Space. The binary .ffp doesn't align to the plugin's 141
# params, so these raw values were solved to hit the exact knob readings from the plugin UI
# (each verified against string_value): Space 0.56 s, Decay Rate 65.3%, Distance 56.3%,
# Brightness 32.7%, Character 76%, Thickness 0%, Stereo Width 65.4%, Predelay 17.36 ms,
# Ducking 0 dB, Mix 33.9%, style Modern; decay-rate EQ bells at 279 Hz/116.9% and
# 8060 Hz/40.2% (Q1); post EQ: −6.03 dB bell at 1409 Hz (Q1) + 10633 Hz high-cut (Q0.968,
# 12 dB/oct). Verified: a 560 ms tail decaying −26 dB@50ms → −55 dB@300ms.
PRO_R2_VOCAL_RICH_SPACE = {
    "space": 0.14570000022649587, "decay_rate": 0.34631632268428625,
    "distance": 0.5625000298023242, "brightness": 0.663249999284746,
    "character": 0.7594999969005567, "thickness": 0.49997501075267614,
    "stereo_width": 0.5448749959468824, "predelay": 0.31570000946521937,
    "ducking": 0.0, "mix": 0.3385000079870206, "style": 0.0,
    "decay_eq_band_1_used": 1.0, "decay_eq_band_1_enabled": 1.0,
    "decay_eq_band_1_frequency": 0.415745213627817, "decay_eq_band_1_rate": 0.8061644136905688,
    "decay_eq_band_1_q": 0.4999165385961515, "decay_eq_band_1_shape": 0.0,
    "decay_eq_band_2_used": 1.0, "decay_eq_band_2_enabled": 1.0,
    "decay_eq_band_2_frequency": 0.8358444869518262, "decay_eq_band_2_rate": 0.42145140469074427,
    "decay_eq_band_2_q": 0.4999165385961515, "decay_eq_band_2_shape": 0.0,
    "post_eq_band_1_used": 1.0, "post_eq_band_1_enabled": 1.0,
    "post_eq_band_1_frequency": 0.6180100142955762, "post_eq_band_1_gain": 0.3994166404008883,
    "post_eq_band_1_q": 0.4999322146177274, "post_eq_band_1_shape": 0.0,
    "post_eq_band_2_used": 1.0, "post_eq_band_2_enabled": 1.0,
    "post_eq_band_2_frequency": 0.8704429566860181, "post_eq_band_2_q": 0.4955217093229276,
    "post_eq_band_2_shape": 1.0, "post_eq_band_2_slope": 0.25,
}


# u-he Satin · Studio Mode / "SE Master Buss -18dB RMS" (Sascha Eversmeier, Satin Factory 1.0),
# applied to the summed mix — voice + music together — as the master-buss tape stage.
# Satin's .h2p is plain text in real units, so these are direct reads converted with each
# parameter's own endpoints and verified against string_value: 15 ips, Modern tape, IEC/CCIR
# record + repro EQ, +8 dB in / +0.29 dB out, 0 VU = -6 dB, 9 dB headroom, auto-makeup on,
# hard Clip output, hiss -70 dB, asperity -75 dB, wow/flutter 10%, 2 repro heads (head 1 at
# 2.5 mm). The h2p's eqpost uses the same 5-curve enum as eqpre, but the plugin's repro_eq
# list prepends "Same as Rec" — hence the +1 offset that lands both on IEC 15 ips (confirmed
# against the factory preset literally named "SR Master Buss1 (IEC 15 IPS)"). The flanger
# fade parameters in the h2p are left at plugin defaults: mode is Studio, so they are inert.
SATIN_SE_MASTER_BUSS_18 = {
    "input_gain": 0.7,                      # +8.00 dB
    "output_gain": 0.5120833333333333,      # +0.29 dB
    "speed_ips": 0.4667614646285105,        # 15 ips
    "bias": 0.5,                            # 0.00
    "pre_emphasis": 0.5,                    # 50%
    "headroom": 0.5,                        # 9 dB
    "0vu_ref": 0.75,                        # -6 dB
    "tape_hiss": 0.625,                     # -70 dB  (preset note: "adjust to taste")
    "asperity": 0.5,                        # -75 dB
    "wow_flutter": 0.1,                     # 10%
    "rec_eq": 0.49875, "repro_eq": 0.59875,  # IEC 15 ips both (CCIR)
    "tape_type": 0.75,                      # Modern
    "auto_makeup": 0.75,                    # On
    "out_clip": 0.75,                       # Clip
    "bypass": 0.24875, "bypass_tape": 0.24875,  # Off
    "repro_heads": 0.24875,                 # 2 Heads
    "head_1_distance": 0.3125, "head_1_level": 1.0, "head_1_bal": 0.5,   # 2.5 mm, 100%
    "head_2_distance": 0.0, "head_2_level": 0.0, "head_2_bal": 0.5,
    "head_3_distance": 0.0, "head_3_level": 0.0, "head_3_bal": 0.5,
    "head_4_distance": 0.0, "head_4_level": 0.0, "head_4_bal": 0.5,
    "head_1_mod_rate": 0.02255639097744361, "head_1_mod_amt": 0.0,       # 1 Hz, 0%
    "head_2_mod_rate": 0.02255639097744361, "head_2_mod_amt": 0.0,
    "head_3_mod_rate": 0.02255639097744361, "head_3_mod_amt": 0.0,
    "head_4_mod_rate": 0.02255639097744361, "head_4_mod_amt": 0.0,
    "bump": 0.5, "gap_width": 0.5,          # 50%, 3.0
    "feedback": 0.0, "mix": 1.0,            # no delay feedback, 100% wet
    "feedback_highpass": 0.015151515151515152, "feedback_lowpass": 0.494949494949495,
    "feedback_limit": 0.75,                 # On
    "mode": 0.12375,                        # Studio (not Flange)
    "delay_routing": 0.0825,                # Multi-Mono
    "encoder": 0.04875, "decoder": 0.04125,  # companding off
    "flng_trigger": 0.75, "flng_phase_invert": 0.24875, "flng_manual_flange": 0.0,
}

PRESET_CALIBRATION_RMS = -18.0  # the RMS level this Satin preset is voiced for, per its name


# Load-time assertions. Every preset here is reconstructed by writing normalized parameter
# values, which are positional in spirit even though addressed by name — so a plug-in update
# that renames, reorders or rescales a parameter would silently change the master with no
# error at all. These are a handful of readouts per plug-in, checked on every render, so
# drift fails loudly instead of quietly. Regenerate them if you intentionally retune a preset.
EXPECT = {
    "PRO_DS_FEMALE_WIDE_BAND": {"high_pass_frequency": "7000.5 Hz", "low_pass_frequency": "14000 Hz",
                                "range": "6.00 dB", "band_processing": "Wide Band"},
    "PRO_DS_T_TRANSIENT": {"high_pass_frequency": "3556.6 Hz", "low_pass_frequency": "7000.5 Hz",
                           "threshold": "-36.00 dB"},
    "PRO_C3_OPEL": {"style": "Op-El", "threshold": "-14.00 dB", "ratio": "3.50:1",
                    "oversampling": "32x"},
    "SOOTHE2_VOCAL_DEHARSH_1": {"depth": "4.1", "band2_freq_hz": "2k4", "band2_sens_db": "10.8",
                                "mix": "49.6", "low_cut_freq_hz": "249", "high_cut_freq_hz": "7k5"},
    "PRO_R2_VOCAL_RICH_SPACE": {"space": "559.9 ms", "decay_rate": "65.30%", "character": "75.9%"},
    "SATIN_SE_MASTER_BUSS_18": {"input_gain": "8.00", "speed_ips": "15.00", "rec_eq": "IEC 15 ips",
                                "repro_eq": "IEC 15 ips", "tape_type": "Modern",
                                "out_clip": "Clip", "0vu_ref": "-6.00"},
    "RX_PODCAST_PLOSIVES": {"sensitivity": "5.73", "strength": "4.70",
                            "frequency_limit_hz": "250.00"},
}


def load_vst(path: Path, settings: dict, expect: str | None = None):
    # Load a VST3 and reconstruct a preset by setting named raw parameters (pedalboard
    # can't load FabFilter .ffp / iZotope .xml / u-he .h2p preset files, so we set params
    # directly). Unknown names and drifted readouts are hard errors: silently skipping
    # either would render a different master with no indication anything was wrong.
    plug = load_plugin(str(path))
    unknown = [n for n in settings if n not in plug.parameters]
    if unknown:
        sys.exit(f"{path.name}: no such parameter(s) {', '.join(sorted(unknown))}. The plug-in's "
                 f"parameter names have changed — the stored preset values no longer apply, and "
                 f"the presets in this file must be re-derived against the new build.")
    for name, raw in settings.items():
        plug.parameters[name].raw_value = raw
    for name, want in EXPECT.get(expect or "", {}).items():
        got = plug.parameters[name].string_value
        if got != want:
            sys.exit(f"{path.name}: preset '{expect}' drifted — {name} reads {got!r}, expected "
                     f"{want!r}. The plug-in's parameter scaling changed; re-derive the preset "
                     f"values rather than shipping a master that no longer matches the preset.")
    return plug


# =============================================================================
# The engine interface mix_music.py drives: preflight → voice_chain → (duck and
# sum, in the driver) → tape_master. mix_chain_oss.py exposes the same four names.
# =============================================================================

def preflight(args) -> None:
    # "Is this engine usable" is a question about the filesystem here, not about the DSP:
    # without the licensed plug-ins there is nothing to render with. soothe2 and Satin are
    # only needed when their stages are not bypassed; RX De-plosive is optional throughout.
    required = [PRO_DS, PRO_C3, PRO_R2]
    if not args.no_deharsh:
        required.append(SOOTHE2)
    if not args.no_tape:
        required.append(SATIN)
    missing = [p.name for p in required if not p.exists()]
    if missing:
        sys.exit(f"Required VST3 plug-in(s) not found in {VST3_DIR}: {', '.join(missing)}. "
                 f"Install them, or render with --engine oss (the default), which rebuilds "
                 f"this chain from pedalboard built-ins and needs nothing installed.")
    if not args.no_self_test:
        audio_dsp.self_test()  # the duck this engine shares with the other one


def voice_chain(voice: np.ndarray, args, steps) -> np.ndarray:
    """(1, N) mono narration → (2, N) coloured voice, through the plug-in chain."""
    deplosive = RX_DEPLOSIVE.exists()  # spectral repair is optional; skip if RX absent
    chain = (["RX De-plosive (Podcast Plosives)"] if deplosive else []) + [
        "Pro-DS (sibilance)", "Pro-DS (t-transient)", "Pro-C 3 (Op-El, 32x)"] + (
        [] if args.no_deharsh else ["soothe2 (Vocal de-harsh 1)"]) + ["Pro-R 2 (Vocal Rich Space)"]
    print("voice chain (vst): " + " → ".join(chain))
    if deplosive:  # first: clean low-end plosives before any tonal/dynamic shaping
        voice = load_vst(RX_DEPLOSIVE, RX_PODCAST_PLOSIVES, "RX_PODCAST_PLOSIVES")(voice, SR)
        steps.mark("RX De-plosive")
    voice = load_vst(PRO_DS, PRO_DS_FEMALE_WIDE_BAND, "PRO_DS_FEMALE_WIDE_BAND")(voice, SR)
    steps.mark("Pro-DS sibilance")
    voice = load_vst(PRO_DS, PRO_DS_T_TRANSIENT, "PRO_DS_T_TRANSIENT")(voice, SR)
    steps.mark("Pro-DS t-transient")
    voice = load_vst(PRO_C3, PRO_C3_OPEL, "PRO_C3_OPEL")(voice, SR)
    steps.mark("Pro-C 3")
    if not args.no_deharsh:  # de-harsh a levelled signal, before the reverb tail is generated
        soothe = load_vst(SOOTHE2, SOOTHE2_VOCAL_DEHARSH_1, "SOOTHE2_VOCAL_DEHARSH_1")
        # --deharsh-mix unset leaves the preset's own stored raw value alone: writing
        # 49.6/100 back would land on 0.496 where the preset stores 0.4957360458374023,
        # so a "default" would silently detune the preset on every render.
        if args.deharsh_mix is not None:
            soothe.parameters["mix"].raw_value = args.deharsh_mix / 100.0
        print(f"de-harsh (soothe2): {soothe.parameters['mix'].string_value}% wet (preset 49.6%)")
        voice = soothe(voice, SR)
        steps.mark("soothe2")
    voice = np.repeat(voice, 2, axis=0)  # → stereo so Pro-R 2 renders a stereo reverb
    pro_r2 = load_vst(PRO_R2, PRO_R2_VOCAL_RICH_SPACE, "PRO_R2_VOCAL_RICH_SPACE")
    pro_r2.parameters["mix"].raw_value = args.reverb_mix / 100.0  # dry the reverb back (preset: 33.9%)
    print(f"reverb (Pro-R 2): {pro_r2.parameters['mix'].string_value} wet (preset 33.9%)")
    voice = pro_r2(voice, SR)  # (2, N) — dry voice + Vocal Rich Space reverb
    steps.mark("Pro-R 2")
    return voice


def tape_master(mix: np.ndarray, args, steps) -> np.ndarray:
    """The summed mix through Satin — voice and music glued together by one tape pass.

    No explicit clip guard here, unlike the OSS engine: this preset's output stage is Satin's
    own hard Clip, so the ceiling is applied inside the plug-in.
    """
    satin = load_vst(SATIN, SATIN_SE_MASTER_BUSS_18, "SATIN_SE_MASTER_BUSS_18")
    if args.tape_hiss is not None:  # the preset's own note: "adjust hiss & noise to taste"
        satin.parameters["tape_hiss"].raw_value = (args.tape_hiss + 120.0) / 80.0
    rms_db = 20.0 * math.log10(float(np.sqrt(np.mean(mix**2))) + EPS)
    print(f"tape (Satin, SE Master Buss -18dB RMS): mix is {rms_db:.1f} dB RMS "
          f"({rms_db - PRESET_CALIBRATION_RMS:+.1f} dB vs the preset's {PRESET_CALIBRATION_RMS:.0f} dB "
          f"voicing), hiss {satin.parameters['tape_hiss'].string_value} dB, "
          f"drive trim {args.tape_drive:+.1f} dB")
    # Drive trim is applied into the tape and taken back out after, so it changes how hard
    # the tape is hit without changing delivered loudness. Off by default: this material
    # already peaks near 0 dBFS, so gaining up to the preset's -18 dB RMS would just clip
    # into Satin's hard-Clip output stage rather than drive the tape harder.
    if args.tape_drive:
        mix = mix * db_to_lin(args.tape_drive)
    mix = satin(mix, SR)
    if args.tape_drive:
        mix = mix * db_to_lin(-args.tape_drive)
    steps.mark("Satin master buss")
    return mix
