"""Bir Chatterbox GPU worker'ının giriş noktası; ana süreçten çağrılır."""

from __future__ import annotations

import json
import sys
from pathlib import Path


_EVENT_PREFIX = "__CHATTERBOX_WORKER__"


def _is_oom_text(text: str | None) -> bool:
    text = (text or "").lower()
    return "out of memory" in text or "cuda oom" in text or "cuda error" in text


def _emit(payload: dict) -> None:
    print(_EVENT_PREFIX + json.dumps(payload, ensure_ascii=True), flush=True)


def _result(index: int, ok: bool, oom: bool, detail: str | None) -> dict:
    item = {"index": index, "ok": ok, "oom": oom, "detail": detail}
    _emit({"type": "result", **item})
    return item


def main(spec_path: str) -> int:
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    items = spec["items"]
    results: list[dict] = []
    _emit({"type": "status", "state": "loading"})
    try:
        from app.tts.chatterbox_provider import ChatterboxTTSProvider

        provider = ChatterboxTTSProvider()
    except Exception as exc:
        detail = str(exc)
        results = [
            _result(int(item["index"]), False, _is_oom_text(detail), detail)
            for item in items
        ]
    else:
        _emit({"type": "status", "state": "ready"})
        for item in items:
            index = int(item["index"])
            _emit({"type": "status", "state": "started", "index": index})
            try:
                provider.synthesize(item["text"], item["voice"], Path(item["outPath"]))
                results.append(_result(index, True, False, None))
            except Exception as exc:
                detail = str(exc)
                results.append(_result(index, False, _is_oom_text(detail), detail))

    result_path = Path(spec["resultPath"])
    temp_path = result_path.with_suffix(result_path.suffix + ".tmp")
    temp_path.write_text(json.dumps({"results": results}, ensure_ascii=False), encoding="utf-8")
    temp_path.replace(result_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
