"""
mix_chain_oss — the open-source voice chain and master buss, built on pedalboard built-ins.

NOT an entry point: a library imported by mix_music.py when `--engine oss` (the default).
Deps come from mix_music.py's uv environment; see audio_dsp.py for why the sibling import works.

The chain, in order: de-plosive (dynamic sub-250 Hz dip) → de-esser (7-14 kHz sibilance) →
a second de-esser tuned to hard "t"/"ts" transients (3.5-7 kHz) → compressor (-14 dB, 3.5:1,
20 dB soft knee) → de-harsh (four dynamic bells) → reverb (small vocal space), then the tape
master buss over the summed mix. The de-harsh sits last before the reverb on purpose: it works
on an already-levelled signal, and the reverb is then fed a de-harshed voice.

WHERE THE NUMBERS CAME FROM
---------------------------
This chain reproduces the six commercial VST3 plug-ins that mix_chain_vst.py still loads
(FabFilter Pro-DS x2 / Pro-C 3 / Pro-R 2, oeksound soothe2, u-he Satin, iZotope RX 12
De-plosive) — ~$1500 of licences that had to be installed at /Library/Audio/Plug-Ins/VST3
before a render could run at all. Every constant below is derived from the preset the plug-in
was running, or from the measurement recorded against it, so each stage reproduces the
*function* of the one it stands in for rather than guessing at a new one. The derivations are
kept inline with each stage: read them before retuning anything. Two stages are honest
approximations rather than ports, and say so at their definitions — the de-harsh (four fixed
bells vs a hundred tracking ones) and the reverb (Freeverb has no early reflections and no tail
modulation). Where the two engines disagree, `--engine vst` is the reference.

Because nothing is loaded from disk, correctness is checked by MEASURING the chain instead of
reading plug-in parameters back: self_test() renders a handful of synthetic signals through
each stage and asserts the result. It runs on every render (--no-self-test skips it) and costs
well under a second. That is strictly stronger than the VST engine's load-time preset
assertions — it also catches a pedalboard version bump moving Freeverb's decay curve, or an
edit swapping the soft-knee compressor for a hard-knee one.
"""

from __future__ import annotations
import math

import numpy as np
from pedalboard import (Distortion, Gain, HighShelfFilter, LowShelfFilter, LowpassFilter,
                        Pedalboard, PeakFilter, Reverb)

import audio_dsp
from audio_dsp import (EPS, SR, advance, assert_between, band_edges, band_pass, block_level_db,
                       db_to_lin, gated_rms_db, gr_report, knee_gr, one_pole_db, rms_db,
                       smooth_gr, subtract_band, upsample_gr, wideband_gain)

NAME = "oss"


# =============================================================================
# The voice chain. One function per stage; each returns (audio, control-rate
# reduction curve) so voice_chain() can print what it did.
# =============================================================================

# --- 1. De-plosive -----------------------------------------------------------
# Was: iZotope RX 12 De-plosive, "Podcast Plosives" (sensitivity 5.73, strength 4.70,
# repair frequency limit 250 Hz; a synthetic 90 Hz "p"-pop measured ~12 dB down with the
# vowel intact). Rebuilt as a dynamic dip below 250 Hz keyed off a TRANSIENT detector, not
# a low-band compressor: a voice's fundamental sits inside the repair band, and a plain
# compressor there would chew the vowels the preset explicitly preserves.
#
# A plosive is a moment when the sub-250 Hz band is far louder than that band ever gets
# while the voice is simply talking — so the detector is the band's level against its own
# MEDIAN OVER THE SPEECH FRAMES. Two nearby designs were tried and rejected, both worth
# recording because each looks right until measured:
#   - the band against its own running average: the average is taken over a dB curve that
#     includes the near-silence between words, which drags it 12-14 dB under the speech
#     level and leaves the detector permanently triggered (98% of frames, pinned at the cap).
#   - the band against the BROADBAND level (a "low-frequency share"): this saturates,
#     because a pop loud enough to matter also raises the broadband reference it is being
#     compared against. Measured, the reduction plateaus near 4 dB no matter how severe the
#     pop gets, which is exactly backwards.
# From the preset: strength 4.70 → cap 2.5*4.70 = 11.75 dB, matching the measured ~12 dB on
# a pop. sensitivity 5.73 → the burst must clear the voice's own low-band median by
# 20 - 5.73 = 14.3 dB. That mapping is calibrated to behaviour, not read off the plug-in: on
# a real narration the low band's own level spans ~10 dB over its median across ordinary
# speech, so the bar has to sit above that and below a genuine burst.
DEPLOSIVE_LIMIT_HZ = 250.0
DEPLOSIVE_SENSITIVITY = 5.73
DEPLOSIVE_STRENGTH = 4.70


def deplosive(voice: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    cap = 2.5 * DEPLOSIVE_STRENGTH
    margin = 20.0 - DEPLOSIVE_SENSITIVITY
    active = block_level_db(voice.mean(axis=0), "peak") > -45.0  # skip the gaps between words
    band = band_pass(voice, 20.0, DEPLOSIVE_LIMIT_HZ, order=2)
    low = block_level_db(band.mean(axis=0), "peak")
    base = float(np.median(low[active])) if active.any() else 0.0
    target = np.where(active, np.clip(low - base - margin, 0.0, None), 0.0).astype(np.float32)
    gr = np.minimum(smooth_gr(target, 0.5, 80.0), cap)
    engaged = gr > 1.0  # count runs, so one pop reads as one event however long it lasts
    events = int(np.count_nonzero(engaged[1:] & ~engaged[:-1]) + engaged[:1].sum())
    # 5 ms lookahead: a plosive's onset is fast, so the dip has to be down before it lands.
    return subtract_band(voice, band, advance(upsample_gr(gr, voice.shape[1]), 5.0)), gr, events


# --- 2 & 3. De-essers --------------------------------------------------------
# Was: two FabFilter Pro-DS instances, both Wide Band — the gain goes on the WHOLE signal,
# which is the point of the second one (softening a "t" burst's punch, not just its top).
# Band edges and ranges are the plug-ins' own readouts, verbatim:
#   sibilance   "Single Vocal / Female Wide Band" — 7000.5 Hz .. 14000 Hz, range 6.00 dB
#   t-transient hand-tuned second stage        — 3556.6 Hz .. 7000.5 Hz, range 9.6 dB
# both with 12 ms lookahead. Pro-DS has no ratio control, so 3.0 is derived from the
# measurement instead: the t-stage measured ~3.9 dB of reduction on the harsh-consonant
# hits, and a burst 6 dB over threshold at 3:1 gives 6*(1-1/3) = 4.0 dB.
#
# THE THRESHOLD IS RELATIVE, and that is a deliberate departure. Both presets carried the
# same threshold, reading -36.00 dB — but that is -36 dB against Pro-DS's own auto-
# referencing detector (which is why one "Single Vocal" preset works across vocals at
# different levels), not -36 dBFS against a raw band peak. Taken literally as dBFS it
# engages on ~70% of frames of a normally-levelled narration and slams both Range caps.
# So it is expressed here as an offset from the narration's own gated speech level, and the
# offset is calibrated to the documented behaviour: at program -3 dB the sibilance stage
# reaches its full 6 dB Range on the loudest sibilants and the t-stage lands ~3.3 dB on the
# hits (documented: ~3.9) with a median of 0.00 dB over the whole file (documented: vowels
# untouched at ~0.01 dB). One shared offset for both stages preserves the presets' own
# structure — they really did carry identical thresholds.
DEESS_THRESHOLD_REL_DB = -3.0
DEESS_SIBILANCE = dict(lo=7000.5, hi=14000.0, range_db=6.0)
DEESS_T_TRANSIENT = dict(lo=3556.6, hi=7000.5, range_db=9.6)


def deess(voice: np.ndarray, lo: float, hi: float, range_db: float,
          threshold_rel_db: float = DEESS_THRESHOLD_REL_DB, ratio: float = 3.0,
          attack_ms: float = 1.0, release_ms: float = 40.0,
          lookahead_ms: float = 12.0) -> tuple[np.ndarray, np.ndarray]:
    threshold_db = gated_rms_db(voice) + threshold_rel_db
    key = band_pass(voice, lo, hi, order=2).mean(axis=0)
    target = knee_gr(block_level_db(key, "peak"), threshold_db, ratio)
    gr = np.minimum(smooth_gr(target, attack_ms, release_ms), range_db)  # Pro-DS "Range"
    return wideband_gain(voice, advance(upsample_gr(gr, voice.shape[1]), lookahead_ms)), gr


# --- 4. Compressor -----------------------------------------------------------
# Was: FabFilter Pro-C 3, Basic / "Op-El" (optical-electro) — -14 dB, 3.50:1, +20 dB soft
# knee, 3.8 ms attack, 180 ms release, auto-gain on. Built here rather than with
# pedalboard.Compressor for one reason: pedalboard's compressor has no knee, and the 20 dB
# knee is most of what Op-El *is*. At -16 dBFS the soft knee gives 1.14 dB of gentle
# leveling where a hard knee gives exactly 0.00, and it is already at full ratio where a
# hard knee has only just engaged. That gliding onset is the optical character; dropping it
# would read as a different compressor, not a different implementation.
# An RMS detector stands in for the optical cell's averaging.
# Not reproduced, deliberately: Op-El's program-dependent dual-stage release (one 180 ms
# release here), and the preset's 32x oversampling — a control-rate gain multiplier has no
# nonlinearity to alias, so there is nothing for oversampling to do.
COMP_THRESHOLD_DB = -14.0
COMP_RATIO = 3.5
COMP_KNEE_DB = 20.0


def compress(voice: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    lvl = block_level_db(voice.mean(axis=0), "rms")
    gr = smooth_gr(knee_gr(lvl, COMP_THRESHOLD_DB, COMP_RATIO, COMP_KNEE_DB), 3.8, 180.0)
    out = wideband_gain(voice, upsample_gr(gr, voice.shape[1]))
    # Auto-gain, measured rather than estimated: match the gated speech RMS to what came
    # in. This makes the stage level-neutral by construction, which is what keeps the
    # music ducker's ABSOLUTE -34 dB threshold downstream meaning what it meant before.
    makeup = float(np.clip(gated_rms_db(voice) - gated_rms_db(out), 0.0, 20.0))
    return out * db_to_lin(makeup), gr, makeup


# --- 5. De-harsh -------------------------------------------------------------
# Was: oeksound soothe2, Vocals / "Vocal de-harsh 1". This is an APPROXIMATION, not a port,
# and the difference is worth knowing: soothe compares ~100 bands against a smoothed
# spectral envelope and follows a resonance as it moves, where this compares four fixed
# bands against their own long-term median. For *this* preset — whose author had already
# committed to four fixed frequencies — the two converge, and the documented behaviour
# (~-0.3 dB median with -3.5 dB on the resonant peaks, action confined to the 249-7503 Hz
# window, +/-0.5 dB outside it) is reproducible. It will not track a resonance that drifts.
#
# Straight from the preset: four bells at 924.4 Hz (sens +9.99 dB, Q 1.279), 2463.5 (+10.76,
# Q 1.541), 5700.9 (+7.18, Q 1.000) and 359.5 (+2.90, Q 1.000), inside a 249.3-7503.1 Hz
# window, depth +4.06 dB, 49.6% wet, +0.98 dB trim, "soft" mode. The window is not
# decoration — it is what makes the 5700 Hz and 359 Hz bells finite. Sensitivity becomes
# how far over its own median share a band must sit before it is touched: high sensitivity
# = low bar, so 924/2463 engage most often and 359 almost never, which is the preset's shape.
# Attack 3 ms / release 30 ms are honest guesses: soothe's 0-10 scale is undocumented.
#
# Two constants are calibrated to the documented behaviour (~-0.3 dB median action with
# -3.5 dB on the resonant peaks) rather than read off the plug-in, because soothe's
# sensitivity scale has no published mapping to dB:
#   DEHARSH_BAR 18 — a band must sit (18 - sens) dB over its own median share before it is
#     touched. At 12 the bells run 0.9-1.7 dB of median reduction, several times the
#     documented action; at 18 they land at 0.03/0.03/0.33/0.00 dB, which is that action.
#   DEHARSH_SMOOTH_MS 15 — the share is smoothed before the gain computer sees it. Without
#     it the detector chases the pitch-period ripple in a 1.45 ms block level and engages
#     on ~95% of frames.
DEHARSH_LO, DEHARSH_HI = 249.3, 7503.1
DEHARSH_BANDS = ((924.4, 1.279, 9.99), (2463.5, 1.541, 10.76),
                 (5700.9, 1.000, 7.18), (359.5, 1.000, 2.90))
DEHARSH_DEPTH_DB = 4.06
DEHARSH_TRIM_DB = 0.98
DEHARSH_MIX_PCT = 49.6
DEHARSH_RATIO = 1.0 / 0.3  # "soft" mode: 0.7 dB of reduction per dB over the bar
DEHARSH_BAR = 18.0
DEHARSH_SMOOTH_MS = 15.0


def deharsh(x: np.ndarray, mix_pct: float) -> tuple[np.ndarray, list[tuple[float, np.ndarray]]]:
    n = x.shape[1]
    window = band_pass(x, DEHARSH_LO, DEHARSH_HI, order=2)
    active = block_level_db(window.mean(axis=0), "rms") > -45.0
    # Detect all four bands from the ORIGINAL signal before touching anything — running
    # them serially would let each detector see the previous band's cut.
    curves: list[tuple[float, float, float, np.ndarray]] = []
    for f0, q, sens in DEHARSH_BANDS:
        lo, hi = band_edges(f0, q)
        lo, hi = max(lo, DEHARSH_LO), min(hi, DEHARSH_HI)
        band = band_pass(x, lo, hi, order=2)
        level = block_level_db(band.mean(axis=0), "rms")
        # Judge the band against the REST of the window, not against the window itself.
        # Measured against the whole window the detector saturates: a resonance loud enough
        # to matter is also most of the window's energy, so the ratio stops growing and the
        # reduction plateaus (0.16 dB whether the resonance is 12 or 20 dB hot). Excluding
        # the band is what makes it "how far does this stick out from its neighbours" —
        # soothe's actual question, asked with four bands instead of a hundred.
        np.subtract(window, band, out=band)  # band's buffer now holds the rest of the window
        share = one_pole_db(level - block_level_db(band.mean(axis=0), "rms"), DEHARSH_SMOOTH_MS)
        del band
        base = float(np.median(share[active])) if active.any() else 0.0
        over = share - base - (DEHARSH_BAR - sens)  # higher sensitivity = lower bar
        gr = np.minimum(smooth_gr(knee_gr(over, 0.0, DEHARSH_RATIO, 3.0), 3.0, 30.0),
                        DEHARSH_DEPTH_DB)
        curves.append((f0, lo, hi, gr))
    del window
    wet = x.copy()
    for _, lo, hi, gr in curves:  # one band array live at a time — these are ~150 MB each
        wet = subtract_band(wet, band_pass(x, lo, hi, order=2), upsample_gr(gr, n))
    m = mix_pct / 100.0
    out = (x * (1.0 - m) + wet * m) * db_to_lin(DEHARSH_TRIM_DB)
    return out, [(f0, gr) for f0, _, _, gr in curves]


# --- 6. Reverb ---------------------------------------------------------------
# Was: FabFilter Pro-R 2, "_2 Small / Vocal Rich Space" — Space 0.56 s, Brightness 32.7%,
# Stereo Width 65.4%, Predelay 17.36 ms, Mix 33.9%; post-EQ a -6.03 dB bell at 1409 Hz (Q1)
# under a 10633 Hz high-cut (12 dB/oct). Rebuilt on pedalboard's Freeverb, wet-only, so the
# predelay and post-EQ can sit on the wet path and the dry/wet blend stays explicit —
# which is what lets --reverb-mix keep meaning exactly what it meant before.
#
# Measured against pedalboard 0.9.24 rather than assumed. Measure the decay over 100-500 ms,
# NOT the 50-300 ms the Pro-R tail was originally quoted over: Freeverb's response has not
# settled into exponential decay by 50 ms (it reads -37.5 dB at 50 ms and -38.2 at 100 ms),
# so a two-point slope starting there lands in the build-up and reports roughly double the
# real decay time.
#   damping = 1 - brightness/100 = 0.673.
#   room_size 0.0 → RT60 565 ms against the preset's 560 ms. Freeverb's decay FLOOR at this
#     damping IS 565 ms and room_size only lengthens it (0.10 gives 622 ms), so the target
#     happens to sit almost exactly on the floor. A shorter tail is not reachable without
#     windowing the wet path.
#   width = 0.654 from Stereo Width. Freeverb's width is not energy-preserving and its wet
#     output runs ~3 dB hotter than the dry it was fed (+2.43 dB at width 0.5, +2.99 at
#     0.654, +4.42 at 1.0), so the wet path is explicitly RMS-matched to the dry before the
#     blend. Without that, --reverb-mix 12 would deliver noticeably more than 12% tail and
#     the number would drift with any width change.
#   PeakFilter(1409, -6.03, Q1) and LowpassFilter(10633) measure as exact matches for the
#     post-EQ; pedalboard's LowpassFilter is 12 dB/oct Butterworth, i.e. Pro-R's Q 0.968.
#   The 8060 Hz decay-EQ bell (shorter HF decay) is already absorbed by damping; the 279 Hz
#     one (longer LF decay) becomes a +1 dB low shelf, a level proxy for a decay-time
#     difference Freeverb cannot express.
# Not reproduced: Distance 56.3% (early reflections) and Character 76% (tail modulation).
# Freeverb exposes neither, and faking them with a chorus on the wet path would be a
# gesture rather than a port. What you get is a plausible small vocal space with the same
# decay time and the same tone — at the default 12% wet, the least audible substitution here.
REVERB_PREDELAY_MS = 17.36
REVERB_PRESET_MIX_PCT = 33.9


def reverb(voice_mono: np.ndarray, mix_pct: float,
           predelay_ms: float = REVERB_PREDELAY_MS) -> np.ndarray:
    dry = np.repeat(voice_mono, 2, axis=0)  # → stereo so the reverb renders a stereo tail
    n = dry.shape[1]
    wet = Pedalboard([Reverb(room_size=0.0, damping=0.673, wet_level=1.0,
                             dry_level=0.0, width=0.654)])(dry, SR)
    # Predelay by array shift, not pedalboard.Delay (which is fractional and mixes) and not
    # on the input (which would truncate the same number of samples off the tail's end).
    lead = int(round(predelay_ms * SR / 1000.0))
    if lead:
        wet = np.concatenate([np.zeros((2, lead), dtype=np.float32), wet], axis=1)[:, :n]
    wet = Pedalboard([LowShelfFilter(cutoff_frequency_hz=279.0, gain_db=1.0, q=0.7),
                      PeakFilter(cutoff_frequency_hz=1409.0, gain_db=-6.03, q=1.0),
                      LowpassFilter(cutoff_frequency_hz=10633.0)])(wet, SR)
    # Calibrate the wet path to the dry, so mix_pct is an honest energy blend rather than a
    # blend with Freeverb's own ~3 dB of output gain baked into it (see the note above).
    dry_rms = float(np.sqrt(np.mean(np.square(dry, dtype=np.float64))))
    wet_rms = float(np.sqrt(np.mean(np.square(wet, dtype=np.float64))))
    if dry_rms > EPS and wet_rms > EPS:
        wet *= dry_rms / wet_rms
    m = mix_pct / 100.0
    return dry * (1.0 - m) + wet * m


# --- 7. Tape master buss -----------------------------------------------------
# Was: u-he Satin, Studio Mode / "SE Master Buss -18dB RMS" — 15 ips, Modern tape, IEC/CCIR
# record + repro EQ, hard-Clip output, hiss -70 dB (re 0 VU, which the preset puts at
# -6 dBFS). Measured on real material it softened peaks ~2.8 dB at constant RMS and added
# 0.24% THD dominated by the 3rd harmonic. Applied to the SUMMED mix — voice and music
# glued by one saturation pass — which is the point of doing it here and not on the voice.
#
# The saturation is pedalboard's Distortion at drive 0, which is a bare tanh, and bare tanh
# lands on both targets almost exactly: measured 0.262% THD at the -18 dBFS calibration
# level with the 5th harmonic 50 dB below the 3rd (odd-harmonic dominated, like a push-pull
# tape stage), and 2.37 dB of peak softening at 0 dBFS. For the record: Distortion's default
# drive of 25 dB measures 15.2% THD, ~60x too much. Analytically, for y = tanh(kx)/k and a
# sine of amplitude a, THD ~= (ka)^2/12 — at a = 0.178 (-18 dBFS RMS) and k = 1 that is
# 0.264%, which is where the measurement comes from. Distortion applies drive as raw gain
# with no makeup, so it is always paired with the inverse Gain.
# IEC/CCIR record and repro EQ are COMPLEMENTARY — building both would just cancel. What is
# actually audible at 15 ips is the head bump and the top-octave loss, which is what the two
# filters are.
# Not reproduced: wow/flutter. The preset's 10% on a 15 ips machine is ~+/-0.03% deviation,
# about 0.5 cent — an order of magnitude under the ~5 cent JND for slow modulation of a
# complex tone. Six lines of fractional-delay LFO that nobody can hear is not worth shipping.
TAPE_DRIVE_DB = 0.0  # tanh's own curve; see --tape-drive to hit it harder
TAPE_HISS_DB = -70.0  # re 0 VU
TAPE_VU_REF_DBFS = -6.0  # this preset's 0 VU
TAPE_CLIP_DBFS = -0.1  # Satin's hard-Clip output stage
PRESET_CALIBRATION_RMS = -18.0  # the level at which the 0.24% THD figure above is specified


def tape_board() -> Pedalboard:
    # Rebuilt per call: pedalboard filters carry state, and the self-test runs first.
    return Pedalboard([
        PeakFilter(cutoff_frequency_hz=55.0, gain_db=1.2, q=1.0),   # 15 ips head bump
        Distortion(drive_db=TAPE_DRIVE_DB),                          # tanh — the whole model
        Gain(gain_db=-TAPE_DRIVE_DB),                                # tanh(kx)/k ⇒ unity small-signal
        HighShelfFilter(cutoff_frequency_hz=12000.0, gain_db=-1.0, q=0.707),  # repro HF loss
    ])


def add_hiss(mix: np.ndarray, hiss_db_vu: float, seed: int = 0) -> float:
    # Decorrelated per channel, seeded so renders stay bit-reproducible (which is what makes
    # the ab/ before-after convention rigorous), and added in place in chunks — a second
    # full-size buffer is another ~300 MB on a 14-minute master.
    level = db_to_lin(hiss_db_vu + TAPE_VU_REF_DBFS)
    rng = np.random.default_rng(seed)
    for i in range(0, mix.shape[1], 1_000_000):
        j = min(i + 1_000_000, mix.shape[1])
        mix[:, i:j] += rng.standard_normal((mix.shape[0], j - i), dtype=np.float32) * level
    return 20.0 * math.log10(level)  # dBFS, for the print


# =============================================================================
# The engine interface mix_music.py drives: preflight → voice_chain → (duck and
# sum, in the driver) → tape_master. mix_chain_vst.py exposes the same four names.
# =============================================================================

def preflight(args) -> None:
    # Nothing to install and nothing to load, so "is this engine usable" is answered by
    # measuring the DSP. The VST engine's equivalent is a plug-in existence check.
    if args.no_self_test:
        return
    audio_dsp.self_test()
    self_test()


def voice_chain(voice: np.ndarray, args, steps) -> np.ndarray:
    """(1, N) mono narration → (2, N) coloured voice, printing what each stage did."""
    chain = ["de-plosive", "de-ess (sibilance)", "de-ess (t-transient)", "compressor"] + (
        [] if args.no_deharsh else ["de-harsh"]) + ["reverb"]
    print("voice chain (oss): " + " → ".join(chain))

    voice, gr, events = deplosive(voice)
    print(gr_report("de-plosive", gr, f", {events} event(s)"))
    steps.mark("de-plosive")
    voice, gr = deess(voice, **DEESS_SIBILANCE)
    print(gr_report("de-ess (sibilance, 7.0-14 kHz)", gr))
    steps.mark("de-ess sibilance")
    voice, gr = deess(voice, **DEESS_T_TRANSIENT)
    print(gr_report("de-ess (t-transient, 3.6-7.0 kHz)", gr))
    steps.mark("de-ess t-transient")
    voice, gr, makeup = compress(voice)
    print(gr_report("compressor (-14 dB, 3.5:1, 20 dB knee)", gr, f", auto-gain {makeup:+.1f} dB"))
    steps.mark("compressor")
    if not args.no_deharsh:  # de-harsh a levelled signal, before the reverb tail is generated
        mix_pct = DEHARSH_MIX_PCT if args.deharsh_mix is None else args.deharsh_mix
        voice, curves = deharsh(voice, mix_pct)
        print(f"de-harsh: {mix_pct:.1f}% wet (preset {DEHARSH_MIX_PCT}%); " +
              ", ".join(f"{f0:.0f} Hz max {c.max():.1f}/med {np.median(c):.2f} dB "
                        f"({100.0 * (c > 0.1).mean():.0f}%)" for f0, c in curves))
        steps.mark("de-harsh")
    voice = reverb(voice, args.reverb_mix)  # (2, N) — dry voice + a small vocal space
    print(f"reverb: {args.reverb_mix:.1f}% wet (preset {REVERB_PRESET_MIX_PCT}%), "
          f"{REVERB_PREDELAY_MS:.1f} ms predelay")
    steps.mark("reverb")
    return voice


def tape_master(mix: np.ndarray, args, steps) -> np.ndarray:
    """The summed mix through the tape buss — voice and music glued by one saturation pass."""
    hiss_db = TAPE_HISS_DB if args.tape_hiss is None else args.tape_hiss
    rms_level = 20.0 * math.log10(float(np.sqrt(np.mean(mix**2))) + EPS)
    # Drive trim is applied into the tape and taken back out after, so it changes how hard
    # the tape is hit without changing delivered loudness. Off by default: this material
    # already peaks near 0 dBFS, so gaining up to the calibration level would just clip
    # into the output stage rather than saturate harder.
    if args.tape_drive:
        mix = mix * db_to_lin(args.tape_drive)
    mix = tape_board()(mix, SR)
    if args.tape_drive:
        mix = mix * db_to_lin(-args.tape_drive)
    hiss_dbfs = add_hiss(mix, hiss_db)
    clip = db_to_lin(TAPE_CLIP_DBFS)
    clipped = int(np.count_nonzero(np.abs(mix) > clip))
    np.clip(mix, -clip, clip, out=mix)
    print(f"tape: mix is {rms_level:.1f} dB RMS "
          f"({rms_level - PRESET_CALIBRATION_RMS:+.1f} dB vs the {PRESET_CALIBRATION_RMS:.0f} dB "
          f"level the THD figure is specified at), drive trim {args.tape_drive:+.1f} dB, "
          f"hiss {hiss_db:.0f} dB re 0 VU = {hiss_dbfs:.0f} dBFS "
          f"({rms_level - hiss_dbfs:.0f} dB under program), {clipped} sample(s) clipped")
    steps.mark("tape master buss")
    return mix


# =============================================================================
# Self-test. The VST engine asserts every preset at load time by reading
# parameters back, because writing normalized values into someone else's plug-in
# fails silently when they rescale a parameter. There is no plug-in to interrogate
# here, but the same failure mode moved rather than disappeared: pedalboard's
# Reverb decay curve and Distortion transfer are undocumented implementation
# details this file has pinned numbers to, and a version bump could move either
# without a word. So the assertions moved too — from reading parameters to
# MEASURING behaviour, which is strictly stronger (it also catches an edit that
# quietly swaps in a hard-knee compressor). Runs on every render; --no-self-test
# skips it; the whole suite is a fraction of a second.
# =============================================================================

def _tone(freq: float, seconds: float, db: float, channels: int = 1) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    amp = db_to_lin(db) * math.sqrt(2.0)  # `db` is RMS
    return np.tile((amp * np.sin(2 * np.pi * freq * t)).astype(np.float32), (channels, 1))


def _burst(freq: float, start: float, length: float, db: float, n: int) -> np.ndarray:
    out = np.zeros((1, n), dtype=np.float32)
    i, m = int(start * SR), int(length * SR)
    t = np.arange(m) / SR
    ramp = np.minimum(1.0, np.minimum(t, (m / SR - t)) / 0.001)  # 1 ms edges
    out[0, i:i + m] = db_to_lin(db) * math.sqrt(2.0) * np.sin(2 * np.pi * freq * t) * ramp
    return out


def thd_percent(y: np.ndarray, f0: float = 1000.0) -> tuple[float, list[float]]:
    x = y[0] if y.ndim > 1 else y
    spec = np.abs(np.fft.rfft(x * np.hanning(x.shape[0])))
    freqs = np.fft.rfftfreq(x.shape[0], 1.0 / SR)
    harmonics = []
    for k in range(1, 7):
        c = int(np.argmin(np.abs(freqs - k * f0)))
        harmonics.append(float(spec[max(0, c - 3):c + 4].max()))
    return 100.0 * math.sqrt(sum(h * h for h in harmonics[1:])) / harmonics[0], harmonics


def self_test() -> None:
    # De-plosive — a 90 Hz pop over a sustained vowel: pop down, vowel untouched. The vowel
    # is harmonic-rich with a weak fundamental, which is what a real one is; a bare sine at
    # the fundamental would put the whole voice inside the repair band and test nothing.
    rng = np.random.default_rng(1)
    n = SR
    vowel = _tone(220.0, 1.0, -34.0)
    for harmonic, level in ((440.0, -26.0), (880.0, -26.0), (1760.0, -28.0), (3520.0, -32.0)):
        vowel = vowel + _tone(harmonic, 1.0, level)
    out, _, events = deplosive(vowel + _burst(90.0, 0.5, 0.06, -10.0, n))
    window = slice(int(0.50 * SR), int(0.56 * SR))
    lo_in = band_pass(vowel + _burst(90.0, 0.5, 0.06, -10.0, n), 20.0, 250.0, 2)[:, window]
    assert_between("de-plosive reduction on a 90 Hz pop",
                   rms_db(lo_in) - rms_db(band_pass(out, 20.0, 250.0, 2)[:, window]),
                   9.0, 13.0, " dB")
    assert_between("de-plosive events on one pop", events, 1, 2)
    quiet = slice(int(0.20 * SR), int(0.40 * SR))
    assert_between("de-plosive effect on a sustained vowel",
                   rms_db(vowel[:, quiet]) - rms_db(out[:, quiet]), -0.3, 0.3, " dB")

    # De-essers — the reduction must reach Range on a burst and vanish between them.
    sib = _tone(220.0, 1.0, -26.0) + _burst(8000.0, 0.5, 0.08, -12.0, n)
    out, gr = deess(sib, **DEESS_SIBILANCE)
    assert_between("de-esser (sibilance) reduction", float(gr.max()), 5.3, 6.7, " dB")
    assert_between("de-esser (sibilance) effect between bursts",
                   rms_db(sib[:, quiet]) - rms_db(out[:, quiet]), -0.1, 0.1, " dB")
    # A 5 kHz burst 6 dB over threshold: 6*(1-1/3) = 4.0 dB, which is what pins ratio 3.0.
    # The detector is a peak detector and _burst takes an RMS level, so -33 dBFS RMS is the
    # -30 dBFS peak that sits 6 dB over the -36 dB threshold.
    t_sig = _burst(5000.0, 0.5, 0.08, -33.0, n)
    _, gr = deess(t_sig, **DEESS_T_TRANSIENT)
    assert_between("de-esser (t-transient) reduction 6 dB over threshold",
                   float(gr.max()), 3.0, 5.0, " dB")

    # Compressor — the -20 dBFS row is the soft knee. A hard knee gives exactly 0.00 there,
    # so this is the assertion that catches someone swapping in pedalboard.Compressor.
    for level, lo_db, hi_db in ((-20.0, 0.14, 0.44), (-6.0, 5.49, 6.09), (-30.0, 0.0, 0.001)):
        _, gr, _ = compress(_tone(1000.0, 1.0, level))
        assert_between(f"compressor gain reduction at {level:.0f} dBFS RMS",
                       float(gr[-1]), lo_db, hi_db, " dB")
    speech = np.concatenate([_burst(300.0, 0.0, 0.3, -12.0, n), np.zeros((1, n // 4), np.float32),
                             _burst(300.0, 0.0, 0.3, -20.0, n)], axis=1)
    out, _, _ = compress(speech)
    assert_between("compressor auto-gain (gated RMS in vs out)",
                   gated_rms_db(out) - gated_rms_db(speech), -0.2, 0.2, " dB")

    # De-harsh — an INTERMITTENT resonance comes down; content outside the 249-7503 Hz
    # window is untouched. Intermittent matters: each band is judged against its own median
    # share of the window, so a resonance present for the whole clip *is* the median and is
    # correctly left alone. That is the stage's defining behaviour, not a limitation to
    # test around — it is what keeps it from turning into a static EQ cut.
    base = band_pass(rng.standard_normal((1, 2 * SR), dtype=np.float32), 200.0, 12000.0)
    base *= db_to_lin(-26.0) / (10 ** (rms_db(base) / 20.0))
    resonant = base.copy()
    resonant[:, int(0.5 * SR):int(1.0 * SR)] += _tone(2463.5, 0.5, -16.0)
    out, curves = deharsh(resonant, DEHARSH_MIX_PCT)
    hot = dict(curves)[2463.5]
    assert_between("de-harsh reduction on a 2463 Hz resonance", float(hot.max()), 3.0, 4.06, " dB")
    steady = deharsh(base + _tone(2463.5, 2.0, -16.0), DEHARSH_MIX_PCT)[1]
    assert_between("de-harsh reduction on a steady resonance (must stay near zero)",
                   float(dict(steady)[2463.5].max()), 0.0, 0.6, " dB")
    above = band_pass(out, 10000.0, 12000.0)
    ref_above = band_pass(resonant * db_to_lin(DEHARSH_TRIM_DB), 10000.0, 12000.0)
    assert_between("de-harsh effect outside its window",
                   rms_db(above) - rms_db(ref_above), -0.15, 0.15, " dB")

    # Reverb — decay time, predelay, wet calibration, and the post-EQ bell.
    imp = np.zeros((1, 2 * SR), dtype=np.float32)
    imp[0, 0] = 1.0
    env = np.abs(reverb(imp, 100.0)).mean(axis=0)  # 100% wet: measure the tail alone

    def _at(t: float) -> float:  # 20 ms window, in the settled part of the decay
        return 20.0 * math.log10(float(env[int((t - 0.01) * SR):int((t + 0.01) * SR)].mean()) + EPS)

    assert_between("reverb RT60", 60.0 * 0.400 / max(_at(0.100) - _at(0.500), EPS) * 1000.0,
                   470.0, 670.0, " ms")
    # Predelay measured as a shift, not as an absolute onset: Freeverb's own shortest comb
    # is 1116 samples, so the first non-zero output sample is never the predelay itself.
    def _onset(ms: float) -> int:
        e = np.abs(reverb(imp, 100.0, predelay_ms=ms)).mean(axis=0)
        return int(np.argmax(e > e.max() * 1e-3))

    assert_between("reverb predelay", _onset(REVERB_PREDELAY_MS) - _onset(0.0),
                   756.0, 776.0, " samples")
    speechy = band_pass(rng.standard_normal((1, SR), dtype=np.float32), 200.0, 8000.0)
    assert_between("reverb wet calibration (100% wet vs dry, RMS)",
                   rms_db(reverb(speechy, 100.0)) - rms_db(np.repeat(speechy, 2, axis=0)),
                   -0.1, 0.1, " dB")
    # Post-EQ: probe with noise, not tones. Freeverb is a comb/allpass bank, so its gain at
    # any single frequency is a mode of the network rather than a property of the EQ — a
    # two-tone probe reports whatever those two modes happen to be doing. Broadband noise
    # averages the modes out and leaves the filter shape.
    tail = reverb(band_pass(np.random.default_rng(7).standard_normal((1, 2 * SR),
                                                                     dtype=np.float32), 200.0,
                            15000.0), 100.0)

    def _sixth_octave(x: np.ndarray, f: float) -> float:
        return rms_db(band_pass(x, f * 2 ** (-1 / 12), f * 2 ** (1 / 12)))

    ref = band_pass(np.random.default_rng(7).standard_normal((1, 2 * SR), dtype=np.float32),
                    200.0, 15000.0)
    tf = {f: _sixth_octave(tail, f) - _sixth_octave(ref, f)
          for f in (700.0, 1409.0, 2800.0, 4000.0, 12000.0)}
    assert_between("reverb post-EQ bell at 1409 Hz (vs an octave either side)",
                   tf[1409.0] - (tf[700.0] + tf[2800.0]) / 2.0, -7.5, -4.0, " dB")
    assert_between("reverb post-EQ high-cut (12 kHz vs 4 kHz)",
                   tf[12000.0] - tf[4000.0], -7.0, -2.5, " dB")

    # Tape — THD, odd-harmonic dominance, peak softening, hiss level.
    board = tape_board()
    sine = _tone(1000.0, 1.0, PRESET_CALIBRATION_RMS, channels=2)
    thd, harm = thd_percent(board(sine, SR))
    assert_between("tape THD at the calibration level", thd, 0.15, 0.45, " %")
    odd = 20.0 * math.log10(harm[2] / (harm[1] + EPS))
    assert_between("tape odd-harmonic dominance (h3 over h2)", odd, 20.0, 200.0, " dB")
    hot_sine = _tone(1000.0, 0.2, -3.0103, channels=2)  # 0 dBFS peak
    softening = 20.0 * math.log10(float(np.abs(hot_sine).max() /
                                        np.abs(tape_board()(hot_sine, SR)).max()))
    assert_between("tape peak softening at 0 dBFS", softening, 1.8, 3.2, " dB")
    silence = np.zeros((2, SR), dtype=np.float32)
    add_hiss(silence, TAPE_HISS_DB)
    assert_between("tape hiss level", rms_db(silence), -77.0, -75.0, " dBFS")
