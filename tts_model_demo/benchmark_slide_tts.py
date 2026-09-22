"""Aynı gerçek slaytlarla Chatterbox ve Coqui GPU hız benchmark'ı."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = Path(__file__).resolve().parent / "public" / "reference.wav"
OUT = ROOT / "_cache" / "tts_benchmarks"


def _python_for(provider: str) -> Path:
    if provider == "chatterbox":
        return ROOT / "chatterbox_venv" / "Scripts" / "python.exe"
    return Path(sys.executable)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("chatterbox", "coqui"), required=True)
    parser.add_argument("--workers", type=int, required=True)
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--slides", type=int, default=16)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    if args.workers < 1 or args.workers > 3:
        raise SystemExit("Worker sayısı 1–3 arasında olmalı.")
    if args.provider == "chatterbox" and args.workers > 2:
        raise SystemExit("Chatterbox 3× bu 8 GB RTX 4060 üzerinde güvenli değil; en fazla 2 worker kullanılabilir.")
    if not REFERENCE.exists():
        raise SystemExit(f"Referans ses bulunamadı: {REFERENCE}")

    slides = json.loads(args.script.read_text(encoding="utf-8"))[: args.slides]
    if len(slides) != args.slides:
        raise SystemExit(f"İstenen {args.slides} slayt bulunamadı.")
    items = [
        {"index": index, "text": (slide.get("narration") or slide.get("title") or "").strip()}
        for index, slide in enumerate(slides)
    ]
    if any(not item["text"] for item in items):
        raise SystemExit("Boş anlatımlı slayt benchmark'a alınamaz.")

    run_dir = OUT / args.label
    run_dir.mkdir(parents=True, exist_ok=True)
    groups = [[] for _ in range(args.workers)]
    for item in items:
        groups[item["index"] % args.workers].append(item)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    launched_at = time.perf_counter()
    workers = []
    for worker_index, group in enumerate(groups):
        spec_path = run_dir / f"worker-{worker_index}.json"
        result_path = run_dir / f"worker-{worker_index}.result.json"
        for item in group:
            item["voice"] = str(REFERENCE)
            item["outPath"] = str(run_dir / f"slide-{item['index'] + 1:02d}.mp3")
        spec_path.write_text(json.dumps({"provider": args.provider, "items": group, "resultPath": str(result_path)}, ensure_ascii=False), encoding="utf-8")
        process = subprocess.Popen(
            [str(_python_for(args.provider)), "-m", "tts_model_demo.benchmark_worker", str(spec_path)],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=creation_flags,
        )
        workers.append((process, result_path))

    results = []
    for process, result_path in workers:
        process.wait()
        if not result_path.exists():
            raise RuntimeError(f"Worker sonuç yazmadan kapandı: {process.returncode}")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if not result["ok"]:
            raise RuntimeError(result["error"])
        results.append(result)
    finished_at = time.perf_counter()
    ready_at = max(result["readyAt"] for result in results)
    report = {
        "label": args.label,
        "provider": args.provider,
        "workers": args.workers,
        "slides": len(items),
        "words": sum(len(item["text"].split()) for item in items),
        "wallSecondsIncludingLoad": round(finished_at - launched_at, 3),
        "synthesisWallSecondsAfterAllModelsReady": round(finished_at - ready_at, 3),
        "modelLoadSeconds": [round(result["readyAt"] - result["startedAt"], 3) for result in results],
        "audioSeconds": round(sum(item["audioSeconds"] for result in results for item in result["results"]), 3),
        "perSlide": sorted((item for result in results for item in result["results"]), key=lambda item: item["index"]),
    }
    (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
