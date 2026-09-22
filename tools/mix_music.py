#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pedalboard>=0.9", "numpy>=1.24", "scipy>=1.10"]
# ///
"""
mix_music — lay a narration over a looping backing bed, ducked out of its own way.

Companion to eleven_tts.py. Takes a VOICE file (the ElevenLabs narration, or any
mp3/wav/flac) and a --music backing track, and produces one mixed mp3:

  1. VOICE is colored by the chosen ENGINE — a six-stage chain either way: de-plosive →
     de-esser (sibilance) → a second de-esser tuned to hard "t"/"ts" transients → compressor
     → de-harsh → reverb. --no-deharsh bypasses the de-harsh for A/B.

       --engine oss (default)  mix_chain_oss.py — pedalboard built-ins plus the band-split
         and envelope-follower machinery in audio_dsp.py. Nothing to install, nothing to
         license, and the whole chain plus the tape costs well under a second.
       --engine vst            mix_chain_vst.py — the commercial plug-ins the OSS chain was
         derived from (FabFilter Pro-DS x2 / Pro-C 3 / Pro-R 2, oeksound soothe2, u-he Satin,
         iZotope RX 12 De-plosive), loaded from /Library/Audio/Plug-Ins/VST3. Needs ~$1500 of
         licences installed and renders far slower — Satin alone measured ~63% of mix
         wall-clock — but it is the reference the other engine approximates.

     Each engine's file documents its own stages: the OSS one carries the derivation of every
     constant from the preset it stands in for, the VST one the reconstructed presets
     themselves. Read those before retuning anything.
  2. MUSIC becomes one bed and is looped to cover the narration plus a short tail. Given
     several tracks, they are spliced in order — each crossfading into the next — and it is
     the whole sequence that repeats, so a long letter over two tracks goes A, B, A, B
     rather than stranding you in B. Every join, between tracks and at the loop point, is
     the same equal-power (sin/cos) crossfade, because a linear one dips ~3 dB in the
     middle. Each track is first trimmed of its own leading and trailing silence, because
     commercial releases carry seconds of it and splicing straight through would put an
     audible hole exactly where a join should be seamless.

     The bed is then dropped under the voice by a fixed baseline gain and side-chain
     DUCKED. The duck is FREQUENCY-SELECTIVE: one envelope-follower keyed off the
     voice drives a gentle broadband dip (--duck-depth, 8 dB) plus a deeper cut confined
     to the speech band (--carve-depth, 9 dB across --carve-low..--carve-high), so the
     music gets out of the way of intelligibility instead of being pushed away wholesale.
     Measured against the old flat 15 dB duck: -8 dB outside the band, -15.9 dB inside it,
     i.e. the bed keeps 7 dB more body and air. --carve-depth 0 reverts to broadband.
     Ratio/attack/release/threshold are all CLI flags, so it is tuned by ear. This stage is
     engine-independent: it keys off whatever voice the chain produced.
  3. The two are summed (voice centered), then the whole mix runs through the TAPE stage —
     one saturation pass gluing voice and music together, which is why it lives here and
     not on the voice chain (u-he Satin under --engine vst, a tanh buss under --engine oss).
     --no-tape bypasses it; --tape-drive trims into it and back out to hit it harder at the
     same output loudness.
  4. The master is topped and tailed (--fade-in / --fade-out) — the tiled bed otherwise
     starts and stops mid-phrase on a hard cut — then normalised to a delivery loudness
     (default -16 LUFS integrated, -1.5 dBTP ceiling) by a two-pass ffmpeg loudnorm and
     encoded to mp3. Nothing earlier in the chain targets a level — without this the
     master lands wherever the DSP leaves it, which measured -18.9 LUFS, ~3 dB under the
     spoken-word target. linear=true keeps it a single gain change so it adds no dynamics
     on top of the compressor; --no-loudnorm skips the stage, --target-lufs / --true-peak
     move the target.

THE ENGINE INTERFACE
--------------------
Each engine module exposes exactly four names — NAME, preflight(args),
voice_chain(voice, args, steps) and tape_master(mix, args, steps) — so this file branches on
the engine once, at the import, and never again. voice_chain takes (1, N) mono and returns
(2, N) stereo; both print their own stage reports and mark their own timing rows. The stage
FLAGS are shared and generic: --no-deharsh means soothe2 under --engine vst and the four
dynamic bells under --engine oss, and likewise --no-tape / --tape-drive / --tape-hiss for
Satin and the tanh buss. --deharsh-mix and --tape-hiss default to None, meaning "whatever
this engine's own preset says", because a literal default would silently detune soothe2 and
Satin (their stored raw values are not exactly 49.6% and -70 dB).

Everything downstream of the voice chain — the loop, the duck, the sum, the fades, the
loudness — is shared, which is the point: a fix to the ducker lands on both engines.

Usage:
    ./mix_music.py narration.mp3 --music track.flac
    ./mix_music.py narration.mp3 --music first.flac --music second.flac   # in order, then loops
    ./mix_music.py narration.mp3 --music 'first.flac,second.flac'         # the same thing
    ./mix_music.py voice.wav --music track.flac --out mixed.mp3 --engine vst
    ./mix_music.py v.mp3 --music t.flac --music-gain -10 --duck-threshold -30 --voice-gain 1
"""

from __future__ import annotations
import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from pedalboard.io import AudioFile

from audio_dsp import SR, band_pass, db_to_lin, duck_reduction_db, subtract_band
# This tool writes the master, but it does not get to name it alone: eleven_tts.py hands it
# the narration and weekly_review.py reports the result, so the rule is shared. See _names.py.
from _names import master_name
from _overwrite import guard
from _speech import music_tracks
from _term import link, Steps

BITRATE = "192k"  # the one encode after the engine's own: the narration and the mix are PCM
TARGET_LUFS = -16.0  # spoken-word delivery target (Apple/Spotify spoken); ACX prefers -18..-23
TARGET_TP = -1.5  # true-peak ceiling, dBTP — leaves room for lossy-codec inter-sample peaks


def read_audio(path: Path, mono: bool) -> np.ndarray:
    # Read any mp3/wav/flac at SR. Returns (channels, frames); mono collapses to (1, frames).
    with AudioFile(str(path)).resampled_to(SR) as f:
        audio = f.read(f.frames)  # (channels, frames), float32
    if mono:
        audio = audio.mean(axis=0, keepdims=True)
    elif audio.shape[0] == 1:
        audio = np.repeat(audio, 2, axis=0)  # mono source → stereo so it can carry a stereo mix
    return audio.astype(np.float32)


def trim_silence(music: np.ndarray, threshold_db: float = -60.0) -> np.ndarray:
    # Drop leading/trailing digital silence. Commercial tracks routinely carry seconds of
    # it — under a second in front and several behind is ordinary — and tiling
    # straight through that makes the bed vanish for 5 s at every loop point.
    loud = np.max(np.abs(music), axis=0) > db_to_lin(threshold_db)
    nz = np.nonzero(loud)[0]
    return music if nz.size == 0 else music[:, nz[0] : nz[-1] + 1]


def equal_power(n: int) -> tuple[np.ndarray, np.ndarray]:
    """A rising and a falling curve whose squares sum to 1, so a seam holds level.

    A linear crossfade dips ~3 dB in the middle, because two uncorrelated signals at half
    amplitude do not sum to full power. sin/cos is the standard fix and is what both the
    loop seam and the track-to-track seam use, so every join in the bed behaves the same.
    """
    t = np.linspace(0, 1, n, dtype=np.float32)
    return np.sin(t * np.pi / 2), np.cos(t * np.pi / 2)


def splice(tracks: list[np.ndarray], crossfade: float = 2.0) -> np.ndarray:
    """Lay several backing tracks end to end, each fading into the next.

    The result is one bed, which `loop_to_length` then treats exactly as it treats a single
    track: if the narration outlasts the whole sequence, the sequence repeats — so with two
    tracks you get A, B, A, B rather than B looping alone once A is spent.

    Each track is trimmed of its own leading and trailing silence first. Commercial releases
    carry seconds of it, and without trimming the "crossfade" would be a fade into silence
    followed by a fade out of it — an audible hole exactly where the join should be seamless.
    """
    trimmed = [trim_silence(t) for t in tracks]
    bed = trimmed[0]
    for nxt in trimmed[1:]:
        # Never eat more than a quarter of either neighbour: a 90-second interlude should
        # not be half crossfade because the default happens to be long.
        xf = max(0, min(int(crossfade * SR), bed.shape[1] // 4, nxt.shape[1] // 4))
        if xf == 0:
            bed = np.concatenate([bed, nxt], axis=1)
            continue
        rise, fall = equal_power(xf)
        seam = bed[:, -xf:] * fall + nxt[:, :xf] * rise
        bed = np.concatenate([bed[:, :-xf], seam, nxt[:, xf:]], axis=1)
    return bed


def loop_to_length(music: np.ndarray, frames: int, crossfade: float = 2.0,
                   trim: bool = True) -> np.ndarray:
    # Tile the backing track to cover `frames`, equal-power crossfading every seam.
    # A bare np.tile splices the track's end onto its start: with silence at either end
    # the bed drops out, and without it you get a click. Trimming plus a constant-power
    # (sin/cos) crossfade makes the loop continuous either way.
    if trim:
        music = trim_silence(music)
    n = music.shape[1]
    if n >= frames:
        return music[:, :frames]
    xf = max(0, min(int(crossfade * SR), n // 4))  # never eat more than a quarter of the track
    if xf == 0:
        return np.tile(music, (1, math.ceil(frames / n)))[:, :frames]
    fade_in, fade_out = equal_power(xf)
    out = np.zeros((music.shape[0], frames + n), dtype=np.float32)
    stride, pos, first = n - xf, 0, True
    while pos < frames:
        seg = music.copy()
        if not first:
            seg[:, :xf] *= fade_in           # incoming copy rises across the seam
            out[:, pos : pos + xf] *= fade_out  # outgoing copy's tail falls across it
        out[:, pos : pos + n] += seg
        pos += stride
        first = False
    return out[:, :frames]


def apply_fades(mix: np.ndarray, fade_in: float, fade_out: float) -> np.ndarray:
    # Top and tail the master. The tiled bed starts and stops wherever it happens to be,
    # so without these the file begins and ends on a hard cut mid-phrase.
    n = mix.shape[1]
    fi, fo = min(int(fade_in * SR), n // 2), min(int(fade_out * SR), n // 2)
    if fi > 0:
        mix[:, :fi] *= np.sin(np.linspace(0, 1, fi, dtype=np.float32) * np.pi / 2)
    if fo > 0:
        mix[:, -fo:] *= np.cos(np.linspace(0, 1, fo, dtype=np.float32) * np.pi / 2)
    return mix


def loudness_normalize(src: Path, dst: Path, target_i: float, target_tp: float,
                       bitrate: str) -> dict:
    """Two-pass ffmpeg loudnorm: measure, then apply, then encode.

    Nothing earlier in the chain targets a delivery level — the master lands wherever the
    DSP leaves it — so this is the stage that makes the output play at a consistent
    loudness next to anything else. linear=true keeps it a single gain change, so it does
    NOT add dynamics on top of the compressor; ffmpeg only falls back to dynamic mode if
    the gain needed would breach the true-peak ceiling (which it reports, and we surface).
    """
    probe = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(src), "-af",
         f"loudnorm=I={target_i}:TP={target_tp}:LRA=11:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, check=True)
    blob = probe.stderr[probe.stderr.rfind("{"):]
    stats = json.loads(blob[: blob.find("}") + 1])
    ffmpeg("-i", str(src), "-af",
           f"loudnorm=I={target_i}:TP={target_tp}:LRA=11:"
           f"measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
           f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
           f"offset={stats['target_offset']}:linear=true",
           "-ar", str(SR), "-c:a", "libmp3lame", "-b:a", bitrate, str(dst))
    return stats


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


def measure_loudness(path: Path) -> tuple[float, float]:
    # (integrated LUFS, true peak dBTP) — what the file actually delivers.
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af",
         "loudnorm=print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, check=True)
    blob = p.stderr[p.stderr.rfind("{"):]
    s = json.loads(blob[: blob.find("}") + 1])
    return float(s["input_i"]), float(s["input_tp"])


def main() -> None:
    ap = argparse.ArgumentParser(description="Mix a narration over a ducked, looping backing track.")
    ap.add_argument("voice", type=Path, help="Narration audio (mp3/wav/flac).")
    ap.add_argument("--music", action="append", required=True, metavar="PATH[,PATH…]",
                    help="Backing track. Give it more than once, or comma-separate them, for "
                         "several: they play in order, crossfading into each other, and the "
                         "whole sequence loops if the narration outlasts it. Paths that "
                         "themselves contain commas are fine: the separator is resolved "
                         "against the disk, so the longest run that names a real file "
                         "wins over splitting it.")
    ap.add_argument("--out", type=Path,
                    help="Output mp3 (default: the narration's name without its -voice suffix, "
                         "so the master takes the plain name; <stem>-music.mp3 for a narration "
                         "that carries no suffix).")
    # The engine selects the whole voice chain and the tape buss; everything else is shared.
    ap.add_argument("--engine", choices=("oss", "vst"), default="oss",
                    help="Voice chain + tape buss: 'oss' (default) rebuilds the chain from "
                         "pedalboard built-ins and needs nothing installed; 'vst' loads the "
                         "commercial plug-ins it was derived from, which must be present in "
                         "/Library/Audio/Plug-Ins/VST3.")
    ap.add_argument("--voice-stem", type=Path, metavar="PATH",
                    help="Also write the processed voice (post-chain, pre-mix) as a WAV stem, "
                         "so the mix can be rebuilt against another backing track without "
                         "re-running the voice chain.")
    ap.add_argument("--music-gain", type=float, default=-8.0, help="Baseline music level, dB (default: -8).")
    ap.add_argument("--voice-gain", type=float, default=0.0, help="Voice trim after the chain, dB — balances against the duck (default: 0).")
    ap.add_argument("--tail", type=float, default=2.0, help="Seconds of music after the voice ends (default: 2).")
    ap.add_argument("--reverb-mix", type=float, default=12.0,
                    help="Reverb wet mix percent (preset: 33.9; lower = more dry voice through).")
    ap.add_argument("--deharsh-mix", type=float, default=None, metavar="PCT",
                    help="De-harsh wet mix percent (default: the preset's own 49.6).")
    ap.add_argument("--no-deharsh", action="store_true",
                    help="Bypass the de-harsh entirely (oeksound soothe2 under --engine vst) — "
                         "the pre-de-harsh chain, for A/B comparison.")
    # Tape master buss (applied to the summed mix, not the voice chain).
    ap.add_argument("--no-tape", action="store_true",
                    help="Bypass the tape master buss (u-he Satin under --engine vst), for A/B.")
    ap.add_argument("--tape-drive", type=float, default=0.0, metavar="DB",
                    help="Trim into the tape and back out after — drives it harder without "
                         "changing output loudness (default: 0).")
    ap.add_argument("--tape-hiss", type=float, default=None, metavar="DB",
                    help="Tape hiss level re 0 VU, -120..-40 (default: the preset's own -70).")
    # Delivery loudness (the final stage — nothing before it targets a level).
    ap.add_argument("--target-lufs", type=float, default=TARGET_LUFS, metavar="LUFS",
                    help=f"Integrated loudness target (default: {TARGET_LUFS}, spoken word).")
    ap.add_argument("--true-peak", type=float, default=TARGET_TP, metavar="DBTP",
                    help=f"True-peak ceiling (default: {TARGET_TP}).")
    ap.add_argument("--no-loudnorm", action="store_true",
                    help="Skip loudness normalisation — encode at whatever level the chain left.")
    ap.add_argument("--no-self-test", action="store_true",
                    help="Skip the DSP self-test that otherwise runs before every render. Under "
                         "--engine vst the plug-in preset assertions still run: they happen at "
                         "load time and there is nothing to measure before a plug-in is loaded.")
    # Sidechain duck (numpy) — one envelope, two depths. Engine-independent.
    ap.add_argument("--duck-ratio", type=float, default=3.0, help="Duck ratio 2-4 (default: 3).")
    ap.add_argument("--duck-attack", type=float, default=15.0, help="Duck attack ms 10-25 (default: 15).")
    ap.add_argument("--duck-release", type=float, default=450.0, help="Duck release ms 300-600 (default: 450).")
    ap.add_argument("--duck-threshold", type=float, default=-34.0, help="Duck threshold dB (default: -34).")
    ap.add_argument("--duck-depth", type=float, default=8.0, metavar="DB",
                    help="Max broadband duck (default: 8 — gentle, so the bed stays present).")
    ap.add_argument("--carve-depth", type=float, default=9.0, metavar="DB",
                    help="Extra duck applied only across the speech band (default: 9; 0 = off, "
                         "which reverts to a purely broadband duck).")
    ap.add_argument("--carve-low", type=float, default=1000.0, metavar="HZ",
                    help="Speech-band carve lower edge (default: 1000).")
    ap.add_argument("--carve-high", type=float, default=4000.0, metavar="HZ",
                    help="Speech-band carve upper edge (default: 4000).")
    # Loop and top-and-tail.
    ap.add_argument("--loop-crossfade", type=float, default=2.0, metavar="SEC",
                    help="Equal-power crossfade at every join in the bed — between one track "
                         "and the next, and at each loop seam (default: 2). Raise it for a "
                         "longer blend; it is capped at a quarter of either neighbour.")
    ap.add_argument("--fade-in", type=float, default=0.5, metavar="SEC",
                    help="Fade-in on the finished master (default: 0.5).")
    ap.add_argument("--fade-out", type=float, default=2.0, metavar="SEC",
                    help="Fade-out on the finished master (default: 2).")
    args = ap.parse_args()

    if not args.voice.exists():
        sys.exit(f"No such file: {args.voice}")
    # One flat, ordered list however it was given — _speech.py's rule, because the two TTS
    # tools take the same flag and hand it straight here, and three copies of "what is a
    # comma" is how the one that mattered ended up splitting an album name.
    tracks = music_tracks(args.music)
    if not tracks:
        sys.exit("--music was given nothing to play.")
    for path in tracks:
        if not path.exists():
            sys.exit(f"No such file: {path}")
    if shutil.which("ffmpeg") is None:
        sys.exit("ffmpeg is required for the mp3 encode (brew install ffmpeg).")

    # The one place this file branches on the engine. Imported lazily so an --engine oss
    # render never loads the VST module, and vice versa.
    if args.engine == "vst":
        import mix_chain_vst as engine
    else:
        import mix_chain_oss as engine

    out = args.out or master_name(args.voice)
    # Before the plug-ins load and the chain runs, since neither can give back the master
    # that was there. The stem is in the same question — it is written from the same run.
    guard(out, args.voice_stem, what=f"mixing {args.voice.name}")
    out.parent.mkdir(parents=True, exist_ok=True)
    steps = Steps()

    engine.preflight(args)  # plug-ins present / DSP still measures the way it is documented
    if not args.no_self_test:
        steps.mark("self-test")

    # 1. Voice color, mono until the stereo reverb — whichever chain the engine provides.
    voice = read_audio(args.voice, mono=True)  # (1, N)
    steps.mark("read voice")
    voice = engine.voice_chain(voice, args, steps)  # (2, N)
    if args.voice_gain:
        voice = voice * db_to_lin(args.voice_gain)
    if args.voice_stem:  # the colored voice on its own — a remixable stem, 24-bit lossless
        args.voice_stem.parent.mkdir(parents=True, exist_ok=True)
        stem = voice
        stem_peak = float(np.max(np.abs(stem)))
        if stem_peak > 0.99:  # the reverb tail can push past unity; guard before the int encode
            stem = stem * (0.99 / stem_peak)
            print(f"voice stem peak-guarded: {20 * math.log10(0.99 / stem_peak):.1f} dB")
        with AudioFile(str(args.voice_stem), "w", SR, num_channels=2, bit_depth=24) as f:
            f.write(stem)
        print(f"Saved voice stem → {link(args.voice_stem)}")
        steps.mark("voice stem")
    n_voice = voice.shape[1]
    n_total = n_voice + int(args.tail * SR)

    # 2. Music → one bed, looped to length (silence trimmed, seams crossfaded), baseline gain.
    #    Several tracks become a sequence first, so what loops is the whole playlist rather
    #    than whichever track happened to be last.
    sources = [read_audio(path, mono=False) for path in tracks]  # each (2, M)
    raw = sum(s.shape[1] for s in sources)
    bed = splice(sources, crossfade=args.loop_crossfade)
    if len(tracks) > 1:
        print(f"backing track: {len(tracks)} tracks spliced in order — "
              + " → ".join(f"{p.name} ({s.shape[1] / SR:.0f}s)" for p, s in zip(tracks, sources)))
    if bed.shape[1] != raw:
        print(f"backing track: {(raw - bed.shape[1]) / SR:.2f}s absorbed by silence-trimming "
              f"and {len(tracks) - 1} track crossfade(s)")
    music = loop_to_length(bed, n_total, crossfade=args.loop_crossfade, trim=False)
    seams = max(0, math.ceil(n_total / max(bed.shape[1] - int(args.loop_crossfade * SR), 1)) - 1)
    print(f"backing track: bed is {bed.shape[1] / SR:.0f}s for {n_total / SR:.0f}s of programme"
          + (f" — {seams} loop seam(s), {args.loop_crossfade:.1f}s equal-power crossfade"
             if seams else " — long enough, no loop needed"))
    music = music * db_to_lin(args.music_gain)
    steps.mark("music load + loop")

    # 3. Sidechain duck, keyed off the (processed) voice. One shared reduction envelope
    #    drives two depths: a gentle broadband dip so the bed stays present, plus a deeper
    #    cut confined to the speech band so the music gets out of the way of intelligibility
    #    rather than being pushed away wholesale. Full music during the tail.
    duck_key = voice.mean(axis=0)
    gr = duck_reduction_db(duck_key, args.duck_ratio, args.duck_attack,
                           args.duck_release, args.duck_threshold)
    gr = np.concatenate([gr, np.zeros(n_total - n_voice, dtype=np.float32)])
    broad_db = np.minimum(gr, args.duck_depth)
    music = music * np.power(10.0, -broad_db / 20.0)[None, :]
    if args.carve_depth > 0:
        extra_db = np.minimum(gr, args.duck_depth + args.carve_depth) - broad_db
        music = subtract_band(music, band_pass(music, args.carve_low, args.carve_high), extra_db)
        print(f"duck: {broad_db.max():.1f} dB broadband + {extra_db.max():.1f} dB extra across "
              f"{args.carve_low:.0f}-{args.carve_high:.0f} Hz "
              f"(median under speech {np.median(broad_db[:n_voice]):.1f}/"
              f"{np.median((broad_db + extra_db)[:n_voice]):.1f} dB)")
    else:
        print(f"duck: {broad_db.max():.1f} dB broadband, no speech-band carve")
    steps.mark("duck + carve")

    # 4. Mix: the stereo voice (with its reverb) summed with the ducked music.
    voice_out = np.zeros((2, n_total), dtype=np.float32)
    voice_out[:, :n_voice] = voice
    mix = music + voice_out
    steps.mark("sum")

    # 5. Master buss: the tape stage over the whole thing — voice and music glued together
    #    by one saturation pass, which is the point of doing it here and not on the voice.
    if not args.no_tape:
        mix = engine.tape_master(mix, args, steps)

    # 6. Top and tail, before loudness so the measurement sees the delivered shape.
    mix = apply_fades(mix, args.fade_in, args.fade_out)
    steps.mark("fades")

    peak = float(np.max(np.abs(mix)))
    if peak > 0.99:  # guard the intermediate wav; loudnorm sets the delivered level below
        mix *= 0.99 / peak
        print(f"peak-guarded: scaled down {20 * math.log10(0.99 / peak):.1f} dB to avoid clipping.")

    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "mix.wav"
        with AudioFile(str(wav), "w", SR, num_channels=2, bit_depth=32) as f:
            f.write(mix)
        if args.no_loudnorm:
            ffmpeg("-i", str(wav), "-c:a", "libmp3lame", "-b:a", BITRATE, str(out))
        else:
            stats = loudness_normalize(wav, out, args.target_lufs, args.true_peak, BITRATE)
            got_i, got_tp = measure_loudness(out)
            print(f"Loudness: {float(stats['input_i']):+.2f} → {got_i:+.2f} LUFS "
                  f"(target {args.target_lufs:+.1f}), true peak "
                  f"{float(stats['input_tp']):+.2f} → {got_tp:+.2f} dBTP "
                  f"(ceiling {args.true_peak:+.1f})")
            if got_tp > args.true_peak + 0.3:
                print(f"  note: true peak landed above the ceiling — loudnorm fell back to "
                      f"dynamic mode; lower --target-lufs to keep it a pure gain change.")

    steps.mark("loudness + encode")
    print(f"Saved {out.stat().st_size / 1024:.1f} KB → {link(out)}  "
          f"({n_total / SR:.1f}s: {n_voice / SR:.1f}s voice + {args.tail:.1f}s tail)")
    audio_s = n_total / SR
    steps.report(f"timing — mix_music ({engine.NAME}, {audio_s:.0f}s of audio)")


if __name__ == "__main__":
    main()
