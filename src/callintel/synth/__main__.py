"""Generate synthetic Persian support calls with ground truth.

    python -m callintel.synth --out data/synthetic --count 30 --voice edge
    python -m callintel.synth --out data/load --count 200 --voice synthetic --no-edge-cases

Layout:  <out>/calls/*.wav|mp3   <out>/edge_cases/*   <out>/ground_truth/*.json   manifest.json
"""

import argparse
import asyncio
import hashlib
import json
import random
import struct
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from callintel.synth.render import render_call, write_audio
from callintel.synth.scenarios import Scenario, build
from callintel.synth.voices import (
    AGENT_STYLES,
    CUSTOMER_STYLES,
    SR,
    EdgeVoices,
    SyntheticVoices,
    VoiceBackend,
)


class Generator:
    def __init__(self, out: Path, voices: VoiceBackend, rng: random.Random) -> None:
        self.out, self.voices, self.rng = out, voices, rng
        self.gt_dir = out / "ground_truth"
        self.gt_dir.mkdir(parents=True, exist_ok=True)

    async def emit(self, scenario: Scenario, directory: Path, *,
                   transform: Callable[[Path], float | None] | None = None) -> dict[str, Any]:
        directory.mkdir(parents=True, exist_ok=True)
        agent = self.rng.choice(AGENT_STYLES)
        customer = self.rng.choice(CUSTOMER_STYLES[agent.voice])
        call = await render_call(scenario, self.voices, {"agent": agent, "customer": customer},
                                 self.rng)
        path = write_audio(directory / scenario.name, call, scenario.output_format)
        segments, duration = call.segments, call.duration_s
        flags_valid = True
        if transform is not None and (cut := transform(path)) is not None:
            segments = [s for s in segments if s["end"] <= cut]
            duration = cut
            flags_valid = False  # e.g. the closing was cut off; only WER stays meaningful
        info = sf.info(str(path))
        truth = {
            "name": scenario.name,
            "file": str(path.relative_to(self.out)),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "topic": scenario.topic,
            "format": scenario.output_format,
            "channels": info.channels,
            "sample_rate": info.samplerate,
            "duration_s": round(duration, 2),
            "voice_backend": self.voices.name,
            "voices": {"agent": agent.voice, "customer": customer.voice},
            "segments": segments,
            "expected_flags": sorted(scenario.expected_flags) if flags_valid else [],
            "flags_valid": flags_valid,
            "expected_outcome": scenario.expected_outcome,
            "inject": scenario.inject,
        }
        (self.gt_dir / f"{scenario.name}.json").write_text(
            json.dumps(truth, ensure_ascii=False, indent=1))
        return {k: truth[k] for k in ("name", "file", "sha256", "expected_outcome",
                                      "expected_flags", "duration_s")}

    async def edge_cases(self) -> list[dict[str, Any]]:
        edge = self.out / "edge_cases"
        edge.mkdir(parents=True, exist_ok=True)
        entries = []

        long_call = build(self.rng, "edge_long_call", topics=6,
                          output_format="wav_pcm16_8k_stereo", force={"hold": "announced"})
        entries.append(await self.emit(long_call, edge))

        poison = build(self.rng, "edge_poison_crash", output_format="wav_pcm16_8k_stereo")
        poison.inject = {"asr": "crash"}
        poison.expected_outcome = "failed"
        entries.append(await self.emit(poison, edge))

        def truncate(path: Path) -> float:
            data = path.read_bytes()
            keep = 44 + ((len(data) - 44) * 2 // 5) // 4 * 4  # 40% of the PCM16 stereo frames
            path.write_bytes(data[:keep])
            return (keep - 44) / 4 / SR

        truncated = build(self.rng, "edge_truncated", output_format="wav_pcm16_8k_stereo")
        entries.append(await self.emit(truncated, edge, transform=truncate))

        raw: dict[str, tuple[bytes, str]] = {
            "edge_corrupt.wav": (_corrupt_wav(self.rng), "failed"),
            "edge_too_short.wav": (_short_wav(), "failed"),
            "edge_not_audio.wav": ("این فایل صوتی نیست\n".encode() * 20, "rejected_415"),
            "edge_empty.wav": (b"", "rejected_400"),
        }
        for name, (content, outcome) in raw.items():
            path = edge / name
            path.write_bytes(content)
            entries.append({"name": path.stem, "file": str(path.relative_to(self.out)),
                            "sha256": hashlib.sha256(content).hexdigest(),
                            "expected_outcome": outcome, "expected_flags": [],
                            "duration_s": None})
        return entries


def _corrupt_wav(rng: random.Random) -> bytes:
    """Valid RIFF/WAVE magic (passes the gateway sniff) but an unknown codec tag (0x7777)."""
    payload = rng.randbytes(64_000)
    fmt = struct.pack("<4sIHHIIHH", b"fmt ", 16, 0x7777, 2, 8000, 32000, 4, 16)
    data = struct.pack("<4sI", b"data", len(payload)) + payload
    return struct.pack("<4sI4s", b"RIFF", 4 + len(fmt) + len(data), b"WAVE") + fmt + data


def _short_wav() -> bytes:
    import io

    buf = io.BytesIO()
    noise = np.random.default_rng(0).normal(0, 0.05, (int(0.4 * SR), 2)).astype(np.float32)
    sf.write(buf, noise, SR, format="WAV", subtype="PCM_16")
    return buf.getvalue()


async def main_async(args: argparse.Namespace) -> None:
    out = Path(args.out)
    rng = random.Random(args.seed)
    voices: VoiceBackend = (EdgeVoices(Path(args.cache)) if args.voice == "edge"
                            else SyntheticVoices())
    gen = Generator(out, voices, rng)
    manifest = []
    for i in range(1, args.count + 1):
        scenario = build(rng, f"{args.prefix}_{i:04d}")
        entry = await gen.emit(scenario, out / "calls")
        manifest.append(entry)
        print(f"[{i}/{args.count}] {entry['file']}  {entry['duration_s']:.0f}s  "
              f"flags={','.join(entry['expected_flags']) or '-'}", flush=True)
    if args.edge_cases:
        manifest += await gen.edge_cases()
        print(f"edge cases written to {out / 'edge_cases'}")
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    print(f"manifest: {out / 'manifest.json'} ({len(manifest)} files)")


def main() -> None:
    p = argparse.ArgumentParser(prog="callintel.synth", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default="data/synthetic")
    p.add_argument("--count", type=int, default=30)
    p.add_argument("--voice", choices=["edge", "synthetic"], default="edge")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--prefix", default="call")
    p.add_argument("--cache", default="data/tts_cache", help="TTS cache (edge voice only)")
    p.add_argument("--no-edge-cases", dest="edge_cases", action="store_false")
    asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    main()
