"""Small image-input smoke test. Synthetic cards do not measure artwork recognition."""
import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path
from app.museum.config import MuseumSettings, ROOT
from app.museum.engine import MuseumEngine
from app.museum.retrieval import MuseumIndex
from app.museum.vision import PhotoRecognizer
from app.storage.store import MemoryStore


async def run(manifest: Path, case_id: str | None, output: Path):
    settings = MuseumSettings()
    if not settings.deepseek_api_key:
        raise SystemExit("Local API key is required.")
    cases = json.loads(manifest.read_text(encoding="utf-8"))
    if case_id:
        cases = [c for c in cases if c["id"] == case_id]
    if not 1 <= len(cases) <= 6:
        raise SystemExit("This bounded smoke test accepts at most six cases.")
    # Validate every input before making paid calls.
    inputs = []
    for case in cases:
        raw = (ROOT / case["path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != case["sha256"]:
            raise SystemExit(f"Fixture changed: {case['id']}")
        inputs.append(raw)
    store = MemoryStore()
    index = MuseumIndex(settings, store)
    await index.start()
    recognizer = PhotoRecognizer(MuseumEngine(settings, store, index))
    report = {"kind": "image-input development smoke, not recognition accuracy",
              "model": settings.deepseek_model, "thinking": "disabled",
              "corpus_sha256": index.corpus_hash, "prompt_version": PhotoRecognizer.PROMPT_VERSION,
              "case_filter": case_id,
              "cases_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
              "generated_at": time.time(), "results": []}
    for case, raw in zip(cases, inputs):
        started = time.perf_counter()
        result = await recognizer.recognize(raw, {"_id": "photo-smoke"})
        ids = [r["id"] for r in result["candidates"]]
        passed = result["status"] == case["expected_status"] and (
            ids == [case["expected_source"]] if case["expected_source"] else ids == [])
        trace = await store.get("museum_photo_traces", result["trace_id"])
        report["results"].append({"id": case["id"], "fixture_kind": case["fixture_kind"],
            "expected_source": case["expected_source"], "expected_status": case["expected_status"],
            "matched_expectation": passed, "latency_ms": round((time.perf_counter()-started)*1000),
            "result": result, "trace": trace})
        print(json.dumps({"id": case["id"], "status": result["status"],
                          "matched_expectation": passed}, ensure_ascii=False), flush=True)
        # Keep completed cases even if a later request is interrupted.
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--manifest", type=Path, default=ROOT / "eval/synthetic-photo-cases.json")
    parser.add_argument("--case", help="Explicitly select a development case; recorded in the report")
    parser.add_argument("--output", type=Path, default=ROOT / "eval/photo-smoke.json")
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to allow a small number of paid image API requests.")
    asyncio.run(run(args.manifest, args.case, args.output))
