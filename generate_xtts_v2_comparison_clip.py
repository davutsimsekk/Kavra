from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

# Importing config first keeps the already-downloaded XTTS model under the
# project's _cache directory instead of using the Windows profile cache.
from app import config as _config  # noqa: F401
from app.tts.coqui_provider import _patch_xtts_audio_loading


MAX_ATTEMPTS = 5
PAUSE_SECONDS = 0.22


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate one validated XTTS v2 comparison clip")
    parser.add_argument("spec", type=Path)
    return parser.parse_args()


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def generate_segment(model, segment: str, gpt_cond_latent, speaker_embedding, seed: int) -> tuple[np.ndarray, int, int]:
    minimum_seconds = max(0.9, len(segment.split()) / 4.5)
    best = np.zeros(0, dtype=np.float32)
    best_seed = seed

    for attempt in range(MAX_ATTEMPTS):
        used_seed = seed + attempt
        seed_everything(used_seed)
        result = model.inference(
            segment,
            "tr",
            gpt_cond_latent,
            speaker_embedding,
            temperature=0.75,
            repetition_penalty=10.0,
            top_k=50,
            top_p=0.85,
            speed=1.0,
            enable_text_splitting=False,
        )
        audio = np.asarray(result["wav"], dtype=np.float32).squeeze()
        if audio.size > best.size:
            best = audio
            best_seed = used_seed
        duration = audio.size / 24000
        if duration >= minimum_seconds:
            return audio, used_seed, attempt + 1
        print(
            f"XTTS segment erken kesildi ({duration:.2f} sn < {minimum_seconds:.2f} sn); "
            "farklı seed deneniyor.",
            flush=True,
        )

    return best, best_seed, MAX_ATTEMPTS


def main() -> None:
    args = parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    output = Path(spec["output"]).resolve()
    reference = Path(spec["reference"]).resolve()
    segments = [str(item).strip() for item in spec["segments"] if str(item).strip()]
    base_seed = int(spec["seed"])
    output.parent.mkdir(parents=True, exist_ok=True)

    _patch_xtts_audio_loading()
    from TTS.api import TTS

    use_gpu = torch.cuda.is_available()
    print(f"XTTS v2 yükleniyor (GPU={use_gpu})...", flush=True)
    api = TTS("tts_models/multilingual/multi-dataset/xtts_v2", gpu=use_gpu)
    model = api.synthesizer.tts_model
    gpt_cond_latent, speaker_embedding = model.get_conditioning_latents(audio_path=[str(reference)])

    clips: list[np.ndarray] = []
    details: list[dict[str, int | float]] = []
    for index, segment in enumerate(segments):
        clip, used_seed, attempts = generate_segment(
            model,
            segment,
            gpt_cond_latent,
            speaker_embedding,
            base_seed + index * 100,
        )
        clips.append(clip)
        details.append(
            {
                "segment": index + 1,
                "seed": used_seed,
                "attempts": attempts,
                "duration_seconds": round(clip.size / 24000, 3),
            }
        )

    pause = np.zeros(round(24000 * PAUSE_SECONDS), dtype=np.float32)
    stitched: list[np.ndarray] = []
    for clip in clips:
        if stitched:
            stitched.append(pause)
        stitched.append(clip)
    waveform = np.concatenate(stitched)
    sf.write(output, waveform, 24000, subtype="PCM_16")

    result_path = Path(spec["result"]).resolve()
    result_path.write_text(
        json.dumps(
            {
                "engine": "XTTS v2",
                "filename": output.name,
                "duration_seconds": round(waveform.size / 24000, 3),
                "temperature": 0.75,
                "details": details,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"XTTS hazır: {output}", flush=True)


if __name__ == "__main__":
    main()
