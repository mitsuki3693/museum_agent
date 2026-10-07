"""Paired schema experiment on six already selected developer photos.

Same VLM response passes through frozen v1 and current v2. At most 24 real
requests (6 observations + 18 comparisons), no retries. Raw responses stay in
RAM; private reports contain the usual sanitized traces only, never photos.
"""
import asyncio
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import types

from app.museum.config import MuseumSettings
from app.museum.engine import MuseumEngine
from app.museum.visual_index import MuseumVisualIndex
from app.museum.vision import PhotoRecognizer
from app.museum import partial_verification as current
from app.storage.store import MemoryStore
from summarize_partial_live import classify

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "2430920"
CASES = (1, 4, 5, 8, 11, 12)
OUT = ROOT / "eval/private/part-contract-v2/live.json"


def frozen_verifier():
    git = shutil.which("git") or str(ROOT / ".runtime/tools/mingit/cmd/git.exe")
    source = subprocess.check_output([git, "-c", "safe.directory=" + ROOT.as_posix(),
        "show", BASELINE + ":backend/app/museum/partial_verification.py"], cwd=ROOT)
    module = types.ModuleType("app.museum._frozen_part_verification")
    module.__package__ = "app.museum"
    sys.modules[module.__name__] = module
    exec(compile(source, "<frozen-part-verification>", "exec"), module.__dict__)
    assert module.PROMPT == current.PROMPT, "Only the response contract may change"
    return module, hashlib.sha256(source).hexdigest()


async def main():
    assert not OUT.exists(), "Never overwrite evaluation evidence"
    baseline, baseline_hash = frozen_verifier()
    cfg = MuseumSettings()
    corpus_bytes = cfg.museum_corpus.read_bytes() + b"\n" + cfg.museum_private_corpus.read_bytes()
    records = {r["_id"]: r for path in (cfg.museum_corpus, cfg.museum_private_corpus)
               for r in json.loads(path.read_bytes())}
    store = MemoryStore()
    for row in records.values(): await store.upsert("museum_sources", row)
    visual = MuseumVisualIndex(cfg.museum_visual_manifest, cfg.museum_visual_model, records,
                               cache_dir=cfg.museum_visual_cache)
    await visual.start()
    pack = ROOT / "data/private/MUSE-test-pack-12-20261006"
    cases = json.loads((pack / "manifest.json").read_bytes())

    class Index:
        async def search(self, query): return []

    index = Index()
    index.records = records
    index.corpus_hash = hashlib.sha256(corpus_bytes).hexdigest()
    report = dict(scope="six known developer photos; paired response replay, not online AB",
                  model=cfg.deepseek_model, index_hash=visual.index_hash, corpus_hash=index.corpus_hash,
                  baseline_commit=BASELINE, baseline_module_hash=baseline_hash,
                  current_module_hash=hashlib.sha256((ROOT / "backend/app/museum/partial_verification.py").read_bytes()).hexdigest(),
                  cases=list(CASES), max_model_requests=24, model_requests=0, results=[])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    current_verify, current_version = current.verify, current.VERSION
    try:
        for n in CASES:
            case = cases[n - 1]
            raw = (pack / case["file"]).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == case["sha256"]
            observation = {}
            engine = MuseumEngine(cfg, store, index)
            engine.visual_index = visual
            engine.settings = cfg.model_copy(update={"museum_photo_verification": "visibility",
                                                       "museum_photo_reference_mode": "single"})
            for repeat in range(3):
                # Capture each fresh provider reply once; replay exactly that reply,
                # including exceptions, through the other contract without a retry.
                replies = []
                messages_seen = []
                real = engine._client()

                class Capture:
                    @property
                    def usage_records(self): return real.usage_records
                    async def complete_json(self, messages):
                        observing = messages[0]["content"].startswith("只描述照片")
                        if observing and "value" in observation:
                            value = observation["value"]
                        else:
                            assert report["model_requests"] < 24
                            report["model_requests"] += 1
                            try:
                                value = await real.complete_json(messages)
                            except Exception as exc:
                                value = exc
                            if observing and not isinstance(value, Exception): observation["value"] = value
                        messages_seen.append(messages)
                        replies.append(value)
                        if isinstance(value, Exception): raise value
                        return value

                class Replay:
                    usage_records = []  # No provider call; never interpreted as free inference.
                    def __init__(self): self.position = 0
                    async def complete_json(self, messages):
                        assert messages == messages_seen[self.position]
                        value = replies[self.position]
                        self.position += 1
                        if isinstance(value, Exception): raise value
                        return value

                for mode, verifier, version, factory in (
                    ("v1", baseline.verify, baseline.VERSION, Capture),
                    ("v2", current_verify, current_version, Replay),
                ):
                    current.verify, current.VERSION = verifier, version
                    engine.client_factory = factory
                    started = time.perf_counter()
                    result = await PhotoRecognizer(engine).recognize(raw, {"_id": f"contract-{n}-{repeat}-{mode}"})
                    trace = await store.get("museum_photo_traces", result["trace_id"])
                    report["results"].append(dict(case=n, repeat=repeat, mode=mode,
                        expected=case["expected_source_id"], result=result, trace=trace,
                        replayed=mode == "v2", ms=round((time.perf_counter() - started) * 1000)))
                    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                    print(n, repeat, mode, result["status"], trace["candidate_ids"], trace["error"], flush=True)
                if report["results"][-2]["trace"]["observation_usable"] is None:
                    print("Observation failed; case stopped without retry", n, flush=True)
                    break
    finally:
        current.verify, current.VERSION = current_verify, current_version
    # Verify provenance across each paired response, not just the final counts.
    for a, b in zip(report["results"][::2], report["results"][1::2]):
        assert all(a["trace"][key] == b["trace"][key] for key in
                   ("compared_ids", "visual_scores", "comparison_image_ids"))
    report["complete"] = True
    report["outcomes"] = {mode: dict(Counter(classify(r) for r in report["results"] if r["mode"] == mode))
                          for mode in ("v1", "v2")}
    report["latency_boundary"] = "v2 is response replay; do not compare its latency or token use to v1"
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(requests=report["model_requests"], outcomes=report["outcomes"]), ensure_ascii=True))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
