from __future__ import annotations

import argparse
import html
import inspect
import json
import random
import re
import shutil
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

from chatterbox.mtl_tts import ChatterboxMultilingualTTS


DEFAULT_REFERENCE_NAME = "voice_preview_doga - upbeat and rich.mp3"
DEFAULT_TEXT = (
    "Şimdi küçük ama önemli bir ayrıntıya dikkat edelim! "
    "Bir konuyu gerçekten öğrenmenin en iyi yolu, onu yalnızca dinlemek değil; "
    "kendi cümlelerimizle yeniden anlatmaktır. Peki bunu nasıl yapacağız? "
    "Önce temel fikri anlayacağız, ardından kısa bir örnekle hemen uygulayacağız."
)
DEFAULT_TEMPERATURES = (0.55, 0.65, 0.75, 0.85, 0.95)
DEFAULT_SEED = 20260928
MAX_SEGMENT_ATTEMPTS = 8
PAUSE_SECONDS = 0.22


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Chatterbox Multilingual V3 Turkish temperature comparison demo"
    )
    parser.add_argument("--reference", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("chatterbox_temperature_demo"))
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument(
        "--temperatures",
        type=float,
        nargs="+",
        default=list(DEFAULT_TEMPERATURES),
    )
    parser.add_argument("--exaggeration", type=float, default=0.70)
    parser.add_argument("--cfg-weight", type=float, default=0.30)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu", "mps"), default="auto")
    return parser.parse_args()


def choose_device(requested: str) -> str:
    if requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def find_reference(project_dir: Path, requested: Path | None) -> Path:
    if requested is not None:
        candidate = requested if requested.is_absolute() else project_dir / requested
        if candidate.is_file():
            return candidate.resolve()
        raise FileNotFoundError(f"Referans ses bulunamadı: {candidate}")

    exact_matches = [
        path
        for path in project_dir.rglob(DEFAULT_REFERENCE_NAME)
        if path.is_file() and "chatterbox_temperature_demo" not in path.parts
    ]
    if exact_matches:
        return exact_matches[0].resolve()

    fuzzy_matches = [
        path
        for path in project_dir.rglob("*doga*upbeat*rich*.mp3")
        if path.is_file() and "chatterbox_temperature_demo" not in path.parts
    ]
    if fuzzy_matches:
        return fuzzy_matches[0].resolve()

    raise FileNotFoundError(
        f"'{DEFAULT_REFERENCE_NAME}' proje altında bulunamadı. "
        "Dosya başka yerdeyse --reference ile yolunu belirtin."
    )


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def load_model(device: str) -> tuple[ChatterboxMultilingualTTS, str]:
    loader = ChatterboxMultilingualTTS.from_pretrained
    if "t3_model" in inspect.signature(loader).parameters:
        return loader(device=device, t3_model="v3"), "Chatterbox Multilingual V3"
    print("Uyarı: Kurulu Chatterbox açık V3 seçimini desteklemiyor; paket varsayılanı kullanılacak.")
    return loader(device=device), "Chatterbox Multilingual (kurulu paket varsayılanı)"


def split_text(text: str) -> list[str]:
    segments = [segment.strip() for segment in re.split(r"(?<=[.!?])\s+", text) if segment.strip()]
    return segments or [text.strip()]


def waveform_to_mono_numpy(waveform: torch.Tensor | np.ndarray) -> np.ndarray:
    if isinstance(waveform, torch.Tensor):
        audio = waveform.detach().float().cpu().numpy()
    else:
        audio = np.asarray(waveform, dtype=np.float32)
    audio = np.squeeze(audio)
    if audio.ndim != 1:
        raise ValueError(f"Beklenmeyen ses tensörü boyutu: {audio.shape}")
    return audio.astype(np.float32, copy=False)


def generate_segment_with_retry(
    model: ChatterboxMultilingualTTS,
    segment: str,
    temperature: float,
    base_seed: int,
    exaggeration: float,
    cfg_weight: float,
) -> tuple[np.ndarray, int, int]:
    # Normal Turkish narration is usually below five words/second. This loose lower
    # bound catches near-empty early-EOS generations without rejecting brisk speech.
    minimum_seconds = max(0.9, len(segment.split()) / 4.5)
    best_audio = np.zeros(0, dtype=np.float32)
    best_seed = base_seed

    for attempt in range(MAX_SEGMENT_ATTEMPTS):
        seed = base_seed + attempt
        seed_everything(seed)
        waveform = model.generate(
            segment,
            language_id="tr",
            exaggeration=exaggeration,
            cfg_weight=cfg_weight,
            temperature=temperature,
            repetition_penalty=1.2,
            min_p=0.05,
            top_p=1.0,
        )
        audio = waveform_to_mono_numpy(waveform)
        if audio.size > best_audio.size:
            best_audio = audio
            best_seed = seed
        duration = audio.size / model.sr
        if duration >= minimum_seconds:
            return audio, seed, attempt + 1
        print(
            f"  Erken kesildi ({duration:.2f} sn < {minimum_seconds:.2f} sn); "
            f"segment farklı seed ile yeniden deneniyor."
        )

    print("  Uyarı: Segment tüm denemelerde kısa kaldı; en uzun deneme kullanılacak.")
    return best_audio, best_seed, MAX_SEGMENT_ATTEMPTS


def generate_full_sample(
    model: ChatterboxMultilingualTTS,
    segments: list[str],
    temperature: float,
    base_seed: int,
    exaggeration: float,
    cfg_weight: float,
) -> tuple[np.ndarray, list[dict[str, int]]]:
    pause = np.zeros(round(model.sr * PAUSE_SECONDS), dtype=np.float32)
    parts: list[np.ndarray] = []
    generation_details: list[dict[str, int]] = []
    for segment_index, segment in enumerate(segments):
        segment_seed = base_seed + segment_index * 100
        audio, used_seed, attempts = generate_segment_with_retry(
            model,
            segment,
            temperature,
            segment_seed,
            exaggeration,
            cfg_weight,
        )
        if parts:
            parts.append(pause)
        parts.append(audio)
        generation_details.append({"segment": segment_index + 1, "seed": used_seed, "attempts": attempts})
    return np.concatenate(parts), generation_details


def save_wave(path: Path, waveform: torch.Tensor | np.ndarray, sample_rate: int) -> None:
    audio = waveform_to_mono_numpy(waveform)
    sf.write(path, audio, sample_rate, subtype="PCM_16")


def render_html(
    output_dir: Path,
    text: str,
    reference_name: str,
    results: list[dict[str, object]],
    settings: dict[str, object],
) -> None:
    cards = []
    for index, result in enumerate(results, start=1):
        temperature = float(result["temperature"])
        filename = str(result["filename"])
        duration = float(result.get("duration_seconds", 0.0))
        error = result.get("error")
        if error:
            player = f'<p class="error">Üretilemedi: {html.escape(str(error))}</p>'
            disabled = "disabled"
        else:
            player = f'<audio controls preload="metadata" src="{html.escape(filename)}"></audio>'
            disabled = ""
        cards.append(
            f"""
            <article class="sample-card">
              <div class="sample-head">
                <div><span class="eyebrow">Varyasyon {index} · {duration:.1f} sn</span><h2>Temperature {temperature:.2f}</h2></div>
                <label class="favorite"><input type="radio" name="favorite" value="{temperature:.2f}" {disabled}> En iyi</label>
              </div>
              {player}
              <div class="ratings" data-temperature="{temperature:.2f}">
                <span>Canlılık:</span>
                {''.join(f'<button type="button" data-score="{score}" {disabled}>{score}</button>' for score in range(1, 6))}
              </div>
            </article>
            """
        )

    settings_json = html.escape(json.dumps(settings, ensure_ascii=False, indent=2))
    document = f"""<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Chatterbox Türkçe Temperature Karşılaştırması</title>
  <style>
    :root {{ color-scheme: dark; --bg:#0b0d12; --panel:#151923; --muted:#98a2b3; --line:#293040; --accent:#f5b942; --accent2:#7c5cff; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font:16px/1.55 Inter,Segoe UI,Arial,sans-serif; background:radial-gradient(circle at top left,#211b35 0,transparent 38%),var(--bg); color:#f4f6fb; }}
    main {{ width:min(980px,calc(100% - 32px)); margin:48px auto 80px; }}
    header {{ margin-bottom:28px; }}
    h1 {{ font-size:clamp(2rem,5vw,3.7rem); line-height:1.02; margin:.2rem 0 1rem; letter-spacing:-.045em; }}
    h2 {{ margin:.15rem 0 0; font-size:1.25rem; }}
    .lede,.meta,.hint {{ color:var(--muted); }}
    .badge,.eyebrow {{ color:var(--accent); font-weight:750; letter-spacing:.08em; text-transform:uppercase; font-size:.76rem; }}
    .intro,.sample-card {{ background:color-mix(in srgb,var(--panel) 92%,transparent); border:1px solid var(--line); border-radius:20px; box-shadow:0 20px 60px rgba(0,0,0,.22); }}
    .intro {{ padding:22px; margin-bottom:22px; }}
    .spoken-text {{ font-size:1.05rem; border-left:3px solid var(--accent2); padding-left:16px; margin:18px 0; }}
    .reference {{ display:grid; grid-template-columns:1fr minmax(250px,420px); align-items:center; gap:18px; padding-top:16px; border-top:1px solid var(--line); }}
    .grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; }}
    .sample-card {{ padding:20px; }}
    .sample-head {{ display:flex; justify-content:space-between; gap:16px; align-items:flex-start; }}
    audio {{ width:100%; margin:18px 0 12px; }}
    .favorite {{ display:flex; gap:7px; align-items:center; color:#d9deea; cursor:pointer; white-space:nowrap; }}
    .ratings {{ display:flex; align-items:center; gap:8px; color:var(--muted); flex-wrap:wrap; }}
    button {{ width:34px; height:34px; border-radius:10px; border:1px solid var(--line); color:#f4f6fb; background:#0f1219; cursor:pointer; }}
    button:hover,button.selected {{ border-color:var(--accent); background:#3b2f14; }}
    button:disabled {{ opacity:.35; cursor:not-allowed; }}
    details {{ margin-top:22px; color:var(--muted); }}
    pre {{ white-space:pre-wrap; background:#090b10; border:1px solid var(--line); padding:16px; border-radius:14px; overflow:auto; }}
    .error {{ color:#ff8f8f; }}
    @media (max-width:720px) {{ .grid {{ grid-template-columns:1fr; }} .reference {{ grid-template-columns:1fr; }} main {{ margin-top:28px; }} }}
  </style>
</head>
<body>
<main>
  <header>
    <span class="badge">{html.escape(str(settings["model"]))} · Türkçe</span>
    <h1>Aynı ses, aynı metin.<br>Sadece temperature değişiyor.</h1>
    <p class="lede">Kulaklıkla dinleyin. Telaffuz, doğallık ve canlılık arasında en iyi dengeyi seçin.</p>
  </header>
  <section class="intro">
    <span class="eyebrow">Demo metni</span>
    <p class="spoken-text">{html.escape(text)}</p>
    <div class="reference">
      <div><strong>Referans ses</strong><div class="meta">{html.escape(reference_name)}</div></div>
      <audio controls preload="metadata" src="{html.escape(reference_name)}"></audio>
    </div>
  </section>
  <section class="grid">{''.join(cards)}</section>
  <p class="hint">Puanlar ve “En iyi” seçimi yalnızca bu tarayıcıda saklanır.</p>
  <details><summary>Üretim ayarları</summary><pre>{settings_json}</pre></details>
</main>
<script>
  const storageKey = 'chatterbox-temperature-demo';
  const state = JSON.parse(localStorage.getItem(storageKey) || '{{}}');
  function save() {{ localStorage.setItem(storageKey, JSON.stringify(state)); }}
  document.querySelectorAll('audio').forEach(audio => audio.addEventListener('play', () => {{
    document.querySelectorAll('audio').forEach(other => {{ if (other !== audio) other.pause(); }});
  }}));
  document.querySelectorAll('.ratings').forEach(group => {{
    const temperature = group.dataset.temperature;
    const restore = () => group.querySelectorAll('button').forEach(button => button.classList.toggle('selected', Number(button.dataset.score) === state[temperature]));
    restore();
    group.querySelectorAll('button').forEach(button => button.addEventListener('click', () => {{ state[temperature] = Number(button.dataset.score); save(); restore(); }}));
  }});
  const favorite = document.querySelector(`input[name="favorite"][value="${{state.favorite}}"]`);
  if (favorite) favorite.checked = true;
  document.querySelectorAll('input[name="favorite"]').forEach(input => input.addEventListener('change', () => {{ state.favorite = input.value; save(); }}));
</script>
</body>
</html>
"""
    (output_dir / "index.html").write_text(document, encoding="utf-8")


def main() -> None:
    args = parse_args()
    project_dir = Path(__file__).resolve().parent
    reference = find_reference(project_dir, args.reference)
    output_dir = args.output_dir if args.output_dir.is_absolute() else project_dir / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    reference_copy = output_dir / reference.name
    if reference.resolve() != reference_copy.resolve():
        shutil.copy2(reference, reference_copy)

    device = choose_device(args.device)
    print(f"Cihaz: {device}")
    print(f"Referans: {reference}")
    print(f"Çıktı: {output_dir}")
    model, model_label = load_model(device)
    model.prepare_conditionals(str(reference), exaggeration=args.exaggeration)
    segments = split_text(args.text)
    print(f"Metin {len(segments)} anlamlı parçaya bölündü.")

    results: list[dict[str, object]] = []
    for temperature in args.temperatures:
        filename = f"doga_temp_{temperature:.2f}.wav"
        destination = output_dir / filename
        print(f"Üretiliyor: temperature={temperature:.2f}")
        try:
            waveform, generation_details = generate_full_sample(
                model=model,
                segments=segments,
                temperature=temperature,
                base_seed=args.seed,
                exaggeration=args.exaggeration,
                cfg_weight=args.cfg_weight,
            )
            save_wave(destination, waveform, model.sr)
            results.append(
                {
                    "temperature": temperature,
                    "filename": filename,
                    "duration_seconds": round(waveform.size / model.sr, 3),
                    "generation_details": generation_details,
                }
            )
        except Exception as exc:
            print(f"Hata (temperature={temperature:.2f}): {exc}")
            results.append({"temperature": temperature, "filename": filename, "error": str(exc)})

    settings = {
        "model": model_label,
        "language_id": "tr",
        "reference": reference.name,
        "temperatures": args.temperatures,
        "exaggeration": args.exaggeration,
        "cfg_weight": args.cfg_weight,
        "repetition_penalty": 1.2,
        "min_p": 0.05,
        "top_p": 1.0,
        "seed": args.seed,
        "text_segments": segments,
        "pause_seconds": PAUSE_SECONDS,
        "max_segment_attempts": MAX_SEGMENT_ATTEMPTS,
        "device": device,
    }
    render_html(output_dir, args.text, reference_copy.name, results, settings)
    (output_dir / "manifest.json").write_text(
        json.dumps({"settings": settings, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Demo hazır: {output_dir / 'index.html'}")


if __name__ == "__main__":
    main()
