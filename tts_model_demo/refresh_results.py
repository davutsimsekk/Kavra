"""Üretilmiş demo kartlarını tarayıcı sayfasına anında görünür yapar."""

from __future__ import annotations

import json
from pathlib import Path

from generate_demo import OUT, REFERENCE, SAMPLES


def main() -> None:
    report = {"referenceText": (REFERENCE / "male.txt").read_text(encoding="utf-8").strip(), "samples": []}
    for number, (title, text) in enumerate(SAMPLES, start=1):
        folder = OUT / f"sample-{number}"
        result_path = folder / "results.json"
        if not result_path.exists():
            continue
        results = json.loads(result_path.read_text(encoding="utf-8"))["results"]
        for item in results:
            if item.get("audioFile"):
                item["audioUrl"] = f"sample-{number}/{item['audioFile']}"
        report["samples"].append({"number": number, "title": title, "text": text, "results": results})
    (OUT / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(report['samples'])} kart yayınlandı.")


if __name__ == "__main__":
    main()
