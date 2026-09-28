from __future__ import annotations

import argparse
import html
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import torch

from generate_chatterbox_temperature_demo import (
    DEFAULT_REFERENCE_NAME,
    choose_device,
    find_reference,
    generate_segment_with_retry,
    load_model,
    save_wave,
)


TEXT_SEGMENTS = (
    "Şimdi önemli bir noktaya dikkat edelim!",
    "Bir konuyu öğrenmek, sadece onu dinlemek değildir.",
    "Bilgiyi kendi cümlelerimizle anlatınca bağlantılar daha net görünür.",
    "Hazırsanız, kısa bir örnekle hemen başlayalım.",
)
EXAGGERATIONS = (0.50, 0.65, 0.80, 0.95)
TEMPERATURE = 0.85
CFG_WEIGHT = 0.30
BASE_SEED = 20260929
PAUSE_SECONDS = 0.22


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chatterbox expressiveness and XTTS v2 comparison")
    parser.add_argument("--reference", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("tts_expressiveness_demo"))
    parser.add_argument("--device", choices=("auto", "cuda", "cpu", "mps"), default="auto")
    return parser.parse_args()


def prepare_reference_wav(reference: Path, destination: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(reference),
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            str(destination),
        ],
        check=True,
    )


def generate_xtts(project_dir: Path, output_dir: Path, reference_wav: Path) -> dict[str, object]:
    spec_path = output_dir / "xtts_spec.json"
    result_path = output_dir / "xtts_result.json"
    spec_path.write_text(
        json.dumps(
            {
                "reference": str(reference_wav),
                "output": str(output_dir / "xtts_v2.wav"),
                "result": str(result_path),
                "segments": TEXT_SEGMENTS,
                "seed": BASE_SEED,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    xtts_python = project_dir / "venv" / "Scripts" / "python.exe"
    if not xtts_python.exists():
        raise FileNotFoundError(f"XTTS sanal ortamı bulunamadı: {xtts_python}")
    subprocess.run(
        [str(xtts_python), str(project_dir / "generate_xtts_v2_comparison_clip.py"), str(spec_path)],
        cwd=project_dir,
        check=True,
    )
    return json.loads(result_path.read_text(encoding="utf-8"))


def generate_chatterbox(
    reference: Path,
    output_dir: Path,
    device: str,
) -> tuple[str, list[dict[str, object]]]:
    model, model_label = load_model(device)
    results: list[dict[str, object]] = []
    pause = np.zeros(round(model.sr * PAUSE_SECONDS), dtype=np.float32)

    for exaggeration in EXAGGERATIONS:
        print(f"Chatterbox üretiliyor: exaggeration={exaggeration:.2f}", flush=True)
        model.prepare_conditionals(str(reference), exaggeration=exaggeration)
        clips: list[np.ndarray] = []
        details: list[dict[str, int | float]] = []
        for segment_index, segment in enumerate(TEXT_SEGMENTS):
            clip, used_seed, attempts = generate_segment_with_retry(
                model=model,
                segment=segment,
                temperature=TEMPERATURE,
                base_seed=BASE_SEED + segment_index * 100,
                exaggeration=exaggeration,
                cfg_weight=CFG_WEIGHT,
            )
            clips.append(clip)
            details.append(
                {
                    "segment": segment_index + 1,
                    "seed": used_seed,
                    "attempts": attempts,
                    "duration_seconds": round(clip.size / model.sr, 3),
                }
            )
        stitched: list[np.ndarray] = []
        for clip in clips:
            if stitched:
                stitched.append(pause)
            stitched.append(clip)
        waveform = np.concatenate(stitched)
        filename = f"chatterbox_exaggeration_{exaggeration:.2f}.wav"
        save_wave(output_dir / filename, waveform, model.sr)
        results.append(
            {
                "engine": "Chatterbox",
                "filename": filename,
                "exaggeration": exaggeration,
                "temperature": TEMPERATURE,
                "cfg_weight": CFG_WEIGHT,
                "duration_seconds": round(waveform.size / model.sr, 3),
                "details": details,
            }
        )

    del model
    if device.startswith("cuda"):
        torch.cuda.empty_cache()
    return model_label, results


def segment_summary(details: list[dict[str, object]]) -> str:
    rows = []
    for item in details:
        rows.append(
            f'<li>Cümle {int(item["segment"])}: {float(item["duration_seconds"]):.1f} sn'
            f' · {int(item["attempts"])} deneme</li>'
        )
    return "".join(rows)


def render_html(
    output_dir: Path,
    model_label: str,
    chatterbox_results: list[dict[str, object]],
    xtts_result: dict[str, object],
    reference_name: str,
) -> None:
    chatterbox_cards = []
    for index, result in enumerate(chatterbox_results, start=1):
        exaggeration = float(result["exaggeration"])
        chatterbox_cards.append(
            f"""
            <article class="card">
              <div class="card-head">
                <div><span class="eyebrow">Chatterbox {index}</span><h2>Exaggeration {exaggeration:.2f}</h2></div>
                <label><input type="radio" name="winner" value="chatterbox-{exaggeration:.2f}"> En iyi</label>
              </div>
              <p class="meta">Temperature {TEMPERATURE:.2f} · CFG {CFG_WEIGHT:.2f} · {float(result["duration_seconds"]):.1f} sn</p>
              <audio controls preload="metadata" src="{html.escape(str(result['filename']))}"></audio>
              <details><summary>Cümle bütünlüğü</summary><ol>{segment_summary(result['details'])}</ol></details>
            </article>
            """
        )

    xtts_card = f"""
      <article class="card xtts-card">
        <div class="card-head">
          <div><span class="eyebrow">Diğer motor</span><h2>XTTS v2</h2></div>
          <label><input type="radio" name="winner" value="xtts-v2"> En iyi</label>
        </div>
        <p class="meta">Temperature {float(xtts_result['temperature']):.2f} · aynı referans · {float(xtts_result['duration_seconds']):.1f} sn</p>
        <audio controls preload="metadata" src="{html.escape(str(xtts_result['filename']))}"></audio>
        <details><summary>Cümle bütünlüğü</summary><ol>{segment_summary(xtts_result['details'])}</ol></details>
        <div class="note"><strong>Neyi dinlemeli?</strong><br>Ses benzerliği, Türkçe telaffuz, vurgu çeşitliliği ve cümlelerin eksiksiz okunması.</div>
      </article>
    """
    spoken_text = " ".join(TEXT_SEGMENTS)
    page = f"""<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Chatterbox ve XTTS v2 Karşılaştırması</title>
  <style>
    :root {{ color-scheme:dark; --bg:#090c12; --panel:#141923; --line:#293142; --text:#f5f7fb; --muted:#9aa5b6; --gold:#f3bc4c; --violet:#9277ff; }}
    * {{ box-sizing:border-box }} body {{ margin:0; font:16px/1.5 Inter,Segoe UI,Arial,sans-serif; color:var(--text); background:radial-gradient(circle at 12% 0,#251d3d 0,transparent 35%),var(--bg) }}
    main {{ width:min(1120px,calc(100% - 32px)); margin:44px auto 72px }}
    h1 {{ max-width:850px; font-size:clamp(2.1rem,5vw,4rem); letter-spacing:-.045em; line-height:1.03; margin:.25rem 0 1rem }} h2 {{ margin:.1rem 0;font-size:1.25rem }}
    .lede,.meta {{ color:var(--muted) }} .eyebrow {{ color:var(--gold); text-transform:uppercase; letter-spacing:.09em; font-size:.74rem; font-weight:800 }}
    .intro,.card {{ background:color-mix(in srgb,var(--panel) 94%,transparent); border:1px solid var(--line); border-radius:20px; box-shadow:0 18px 50px rgba(0,0,0,.24) }}
    .intro {{ padding:22px;margin:26px 0 }} .spoken {{ border-left:3px solid var(--violet); padding-left:16px }} .reference {{ display:grid;grid-template-columns:1fr minmax(260px,420px);gap:20px;align-items:center;border-top:1px solid var(--line);padding-top:15px }}
    .comparison {{ display:grid;grid-template-columns:minmax(0,1.3fr) minmax(320px,.8fr);gap:20px;align-items:start }} .stack {{ display:grid;gap:14px }} .card {{ padding:20px }} .card-head {{ display:flex;justify-content:space-between;gap:14px;align-items:flex-start }}
    .xtts-card {{ position:sticky;top:18px;border-color:#544781 }} audio {{ width:100%;margin:14px 0 8px }} label {{ white-space:nowrap;cursor:pointer }} details {{ color:var(--muted);font-size:.92rem }} details ol {{ padding-left:22px }}
    .note {{ margin-top:18px;padding:14px;border-radius:14px;background:#0d1119;border:1px solid var(--line);color:var(--muted) }}
    @media(max-width:820px) {{ .comparison,.reference {{ grid-template-columns:1fr }} .xtts-card {{ position:static }} main {{ margin-top:26px }} }}
  </style>
</head>
<body><main>
  <header><span class="eyebrow">Türkçe TTS laboratuvarı</span><h1>Duygu kontrolü ve motor karşılaştırması</h1><p class="lede">Temperature sabit. Chatterbox'ta yalnızca exaggeration değişiyor; XTTS v2 aynı metni ve aynı Doğa referansını kullanıyor.</p></header>
  <section class="intro"><span class="eyebrow">Eksiksiz okunması gereken metin</span><p class="spoken">{html.escape(spoken_text)}</p><div class="reference"><div><strong>Referans</strong><div class="meta">{html.escape(reference_name)}</div></div><audio controls preload="metadata" src="{html.escape(reference_name)}"></audio></div></section>
  <section class="comparison"><div class="stack"><p class="meta">{html.escape(model_label)}</p>{''.join(chatterbox_cards)}</div><aside>{xtts_card}</aside></section>
</main>
<script>
  const key='tts-expressiveness-winner'; const saved=localStorage.getItem(key); if(saved){{const el=document.querySelector(`input[value="${{saved}}"]`);if(el)el.checked=true;}}
  document.querySelectorAll('input[name="winner"]').forEach(el=>el.addEventListener('change',()=>localStorage.setItem(key,el.value)));
  document.querySelectorAll('audio').forEach(a=>a.addEventListener('play',()=>document.querySelectorAll('audio').forEach(b=>{{if(a!==b)b.pause()}})));
</script></body></html>"""
    (output_dir / "index.html").write_text(page, encoding="utf-8")


def main() -> None:
    args = parse_args()
    project_dir = Path(__file__).resolve().parent
    output_dir = args.output_dir if args.output_dir.is_absolute() else project_dir / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    reference = find_reference(project_dir, args.reference)
    reference_copy = output_dir / DEFAULT_REFERENCE_NAME
    shutil.copy2(reference, reference_copy)
    reference_wav = output_dir / "reference_doga_24khz.wav"
    prepare_reference_wav(reference, reference_wav)

    # XTTS runs first and exits, releasing its CUDA allocation before Chatterbox loads.
    xtts_result = generate_xtts(project_dir, output_dir, reference_wav)
    device = choose_device(args.device)
    model_label, chatterbox_results = generate_chatterbox(reference, output_dir, device)

    manifest = {
        "text_segments": TEXT_SEGMENTS,
        "reference": reference.name,
        "chatterbox_model": model_label,
        "chatterbox": chatterbox_results,
        "xtts": xtts_result,
        "validation": "short independent segments + minimum duration + seed retry",
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    render_html(output_dir, model_label, chatterbox_results, xtts_result, reference_copy.name)
    print(f"Karşılaştırma hazır: {output_dir / 'index.html'}", flush=True)


if __name__ == "__main__":
    main()
