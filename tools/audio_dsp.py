"""
audio_dsp — the band-split / envelope-follower primitives both mixer engines are built from.

NOT an entry point: this is a library imported by mix_music.py and its engine modules
(mix_chain_oss.py, mix_chain_vst.py). It has no shebang and no PEP 723 dependency block —
numpy and scipy come from mix_music.py's uv environment, and sibling imports resolve because
`uv run --script` puts the script's own directory first on sys.path.

Everything dynamic in this pipeline is the same four steps — band-split → block level → gain
computer → attack/release — so there is one place to fix a detector bug and one place to read
the control rate from. The helpers all take (channels, frames) and key off `.mean(axis=0)`, so
one written for the mono voice chain also works on the stereo master buss.

The music ducker lives here rather than in an engine because BOTH engines duck the same way:
only the voice colouring differs between --engine oss and --engine vst, and the duck is keyed
off whatever voice came out of it. That is also why self_test() is run under both engines —
band_pass is what builds the driver's speech-band carve, whichever chain produced the voice.
"""

from __future__ import annotations
import math
import sys

import numpy as np
from scipy.signal import butter, lfilter, sosfiltfilt

SR = 44100  # everything is resampled to this common rate
HOP = 64  # control-rate decimation for the ducker (~1.45 ms at 44.1 kHz — finer than any attack)
EPS = 1e-9


# =============================================================================
# Primitives. Every dynamic stage in either engine is assembled from these four
# steps — band-split → block level → gain computer → attack/release.
# =============================================================================

def band_pass(x: np.ndarray, lo: float | None, hi: float | None,
              order: int = 4) -> np.ndarray:
    """Zero-phase band/low/high-pass (forward-backward, so no phase shift).

    Zero phase matters everywhere this is used: the reduction is built as
    `x - band * (1 - g)`, which is only a clean spectral dip if `band` lines up
    sample-for-sample with what it was taken from. An IIR pass with phase shift would
    comb-filter the sum instead. sosfiltfilt doubles the effective order, so order=2
    already gives a 24 dB/oct skirt — enough for a detector, gentle enough that a
    reduction band reads as a bell rather than a gouge.
    """
    nyq = SR / 2.0
    if lo is not None and hi is not None:
        sos = butter(order, [lo / nyq, hi / nyq], btype="band", output="sos")
    elif hi is not None:
        sos = butter(order, hi / nyq, btype="low", output="sos")
    else:
        sos = butter(order, lo / nyq, btype="high", output="sos")
    # Three periods of the lowest edge. The old fixed `3 * n_sections * 2` was 24 samples
    # — a fifth of a cycle at 250 Hz — which left a real edge transient on low bands.
    edge = lo if lo is not None else hi
    padlen = min(int(3 * SR / edge), x.shape[1] - 1)
    return sosfiltfilt(sos, x, axis=1, padlen=max(padlen, 0)).astype(np.float32)


def band_edges(f0: float, q: float) -> tuple[float, float]:
    # Geometrically symmetric edges of a bell at f0 with quality q: the pair satisfying
    # both f_hi - f_lo == f0/q and f_lo*f_hi == f0**2. Used to turn the de-harsh presets'
    # (frequency, Q) pairs into band-pass edges.
    half = 1.0 / (2.0 * q)
    g = math.hypot(1.0, half)
    return f0 * (g - half), f0 * (g + half)


def block_level_db(x: np.ndarray, detector: str = "peak") -> np.ndarray:
    # One level per HOP samples. "peak" is responsive with no zero-crossing dropouts —
    # right for transient detectors; "rms" averages within the block, which is what an
    # optical compressor's cell sees.
    n = x.shape[0]
    n_blocks = math.ceil(n / HOP)
    blocks = np.pad(x, (0, n_blocks * HOP - n)).reshape(n_blocks, HOP)
    if detector == "peak":
        lvl = np.abs(blocks).max(axis=1)
    else:
        lvl = np.sqrt((blocks.astype(np.float64) ** 2).mean(axis=1))
    return 20.0 * np.log10(lvl + EPS)


def knee_gr(level_db: np.ndarray, threshold_db: float, ratio: float,
            knee_db: float = 0.0) -> np.ndarray:
    # Static gain computer → gain reduction in dB (>= 0). knee_db=0 is the hard knee the
    # ducker has always used; a soft knee spreads the onset over +/- knee/2 around the
    # threshold with the standard quadratic interpolation, which is most of what an
    # optical compressor's character actually is.
    over = level_db - threshold_db
    slope = 1.0 - 1.0 / ratio
    if knee_db <= 0.0:
        return np.where(over > 0.0, over * slope, 0.0)
    half = knee_db / 2.0
    return np.select(
        [over <= -half, over >= half],
        [np.zeros_like(over), over * slope],
        slope * (over + half) ** 2 / (2.0 * knee_db),
    )


def smooth_gr(target: np.ndarray, attack_ms: float, release_ms: float) -> np.ndarray:
    # One-pole attack/release over the control-rate reduction curve. Attack fires when
    # more reduction is needed, release when less.
    ctrl_rate = SR / HOP
    a_att = math.exp(-1.0 / (max(attack_ms, 0.1) * 1e-3 * ctrl_rate))
    a_rel = math.exp(-1.0 / (max(release_ms, 0.1) * 1e-3 * ctrl_rate))
    gr = 0.0
    out = np.empty(target.shape[0], dtype=np.float32)
    for i, want in enumerate(target):
        coeff = a_att if want > gr else a_rel
        gr = coeff * gr + (1.0 - coeff) * want
        out[i] = gr
    return out


def upsample_gr(gr_blocks: np.ndarray, n: int) -> np.ndarray:
    # Block reductions → per-sample, centered on each block, linearly interpolated.
    centers = np.arange(gr_blocks.shape[0]) * HOP + HOP / 2.0
    return np.interp(np.arange(n), centers, gr_blocks).astype(np.float32)


def one_pole_db(level_db: np.ndarray, tau_ms: float) -> np.ndarray:
    # Running average of a control-rate dB curve, seeded at its first value so it doesn't
    # ramp up from silence and read the opening as a transient.
    a = math.exp(-1.0 / (tau_ms * 1e-3 * SR / HOP))
    out, _ = lfilter([1.0 - a], [1.0, -a], level_db, zi=[a * level_db[0]])
    return out


def advance(gr: np.ndarray, ms: float) -> np.ndarray:
    # Offline lookahead: shift the reduction curve earlier so it is already down when the
    # transient arrives. Rendering the whole file at once makes this free and exact —
    # no delay line, no latency to compensate downstream.
    lead = int(round(ms * SR / 1000.0))
    if lead <= 0 or lead >= gr.shape[0]:
        return gr
    return np.concatenate([gr[lead:], np.full(lead, gr[-1], dtype=gr.dtype)])


def wideband_gain(x: np.ndarray, gr_db: np.ndarray) -> np.ndarray:
    # Apply a per-sample reduction to the whole signal (a "wide band" de-esser: detect in
    # a band, duck everything, so the transient's punch softens rather than just its top).
    return x * np.power(10.0, -gr_db / 20.0)[None, :]


def subtract_band(x: np.ndarray, band: np.ndarray, gr_db: np.ndarray) -> np.ndarray:
    # Apply a per-sample reduction to one band only, by taking back part of what the band
    # contributes: x - band + band*g == x - band*(1-g). `band` may come from a different
    # (earlier) signal than `x`, which is how the de-harsh keeps all four of its detectors
    # looking at the untouched input.
    return x - band * (1.0 - np.power(10.0, -gr_db / 20.0))[None, :]


def duck_reduction_db(voice_mono: np.ndarray, ratio: float, attack_ms: float,
                      release_ms: float, threshold_db: float) -> np.ndarray:
    # The music ducker: feed-forward sidechain compressor → per-sample gain REDUCTION in
    # dB (>= 0). Returned as dB rather than linear gain so the broadband duck and the
    # speech-band carve can be driven from one shared envelope with different depths.
    target = knee_gr(block_level_db(voice_mono, "peak"), threshold_db, ratio)
    return upsample_gr(smooth_gr(target, attack_ms, release_ms), voice_mono.shape[0])


def gr_report(label: str, gr_blocks: np.ndarray, extra: str = "") -> str:
    # Every dynamic stage prints this. Retuning a threshold is a question of "is it firing
    # on the right material", which is unanswerable from the audio alone but obvious from
    # max / median / how-often.
    engaged = gr_blocks > 0.1
    median = float(np.median(gr_blocks[engaged])) if engaged.any() else 0.0
    return (f"{label}: max {gr_blocks.max():.1f} dB, median {median:.1f} dB when engaged, "
            f"{100.0 * engaged.mean():.1f}% of frames{extra}")


def gated_rms_db(x: np.ndarray, gate_db: float = -50.0) -> float:
    # Loudness of the parts that are actually speech. An ungated RMS over a narration is
    # dominated by its pauses, so it moves whenever the pause structure moves.
    lvl = block_level_db(x.mean(axis=0), "rms")
    keep = lvl > gate_db
    if not keep.any():
        return -np.inf
    return 10.0 * math.log10(float(np.power(10.0, lvl[keep] / 10.0).mean()) + EPS)


def db_to_lin(db: float) -> float:
    return 10.0 ** (db / 20.0)


# =============================================================================
# Self-test. Correctness here is checked by MEASURING the DSP rather than by
# reading a parameter back, which is strictly stronger: it catches a scipy or
# pedalboard version bump moving a filter's behaviour, and it catches an edit
# that quietly changes what a helper does. mix_chain_oss.py has its own,
# larger suite over the stages built on top of these; this one covers what
# BOTH engines depend on, since the driver's speech-band carve is built from
# band_pass whichever chain coloured the voice.
# =============================================================================

def assert_between(label: str, got: float, lo: float, hi: float, unit: str = "") -> None:
    if not lo <= got <= hi:
        sys.exit(f"self-test: {label} drifted — measured {got:.3f}{unit}, expected "
                 f"{lo:.3f}..{hi:.3f}{unit}. The DSP no longer does what this file "
                 f"documents; fix the stage (or re-derive the bound) rather than "
                 f"shipping a master that does not match its description.")


def rms_db(x: np.ndarray) -> float:
    return 20.0 * math.log10(float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) + EPS)


def self_test() -> None:
    # band_edges — pure algebra, and the one thing the de-harsh bells all depend on.
    lo, hi = band_edges(1000.0, 2.0)
    assert_between("band_edges bandwidth", hi - lo, 499.999, 500.001, " Hz")
    assert_between("band_edges geometric centre", math.sqrt(lo * hi), 999.999, 1000.001, " Hz")

    # band_pass — flat in band, gone an octave out.
    rng = np.random.default_rng(1)
    noise = rng.standard_normal((1, SR), dtype=np.float32)
    band = band_pass(noise, 1000.0, 4000.0)
    in_band = rms_db(band_pass(band, 1500.0, 3000.0)) - rms_db(band_pass(noise, 1500.0, 3000.0))
    out_band = rms_db(band_pass(band, 200.0, 400.0)) - rms_db(band_pass(noise, 200.0, 400.0))
    assert_between("band_pass in-band gain", in_band, -0.5, 0.5, " dB")
    assert_between("band_pass rejection an octave out", out_band, -120.0, -30.0, " dB")
