"""One-reference ablation: old reference vs independently sourced rear view.

Default: local retrieval only. --live: five existing developer photos, three
repeats, at most 35 requests; same candidate IDs/scores, different selected view.
Private source/reference manifests must be prepared and inspected beforehand.
"""
import argparse
import asyncio
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time

from app.museum.config import MuseumSettings
from app.museum.engine import MuseumEngine
from app.museum.visual_index import MuseumVisualIndex
from app.museum.vision import PhotoRecognizer, prepare_image
from app.museum.partial_verification import VERSION
from app.storage.store import MemoryStore
from summarize_partial_live import classify

ROOT = Path(__file__).resolve().parents[1]
NEW_MANIFEST = ROOT / "data/private/va-pilot-100-neptune-back-v1-references.json"
OUT = ROOT / "eval/private/neptune-view-v1"
LIVE_CASES = (3, 4, 7, 8, 12)
REFERENCE_ID = "va-neptune-archival-back-v1"


def assert_single_addition(original, expanded):
    old = {r["id"]: r for r in original["references"]}
    new = {r["id"]: r for r in expanded["references"]}
    assert len(new) == len(expanded["references"])
    assert set(new) - set(old) == {REFERENCE_ID}
    assert all(new.get(k) == v for k, v in old.items())
    assert new[REFERENCE_ID]["source_id"] == "va-neptune-triton"


async def main(live=False):
    cfg = MuseumSettings()
    assert_single_addition(json.loads(cfg.museum_visual_manifest.read_bytes()), json.loads(NEW_MANIFEST.read_bytes()))
    if live: assert not (OUT / "live.json").exists(), "Never overwrite live evidence"
    records = {r["_id"]: r for p in (cfg.museum_corpus, cfg.museum_private_corpus) for r in json.loads(p.read_bytes())}
    corpus_hash = hashlib.sha256(cfg.museum_corpus.read_bytes() + b"\n" + cfg.museum_private_corpus.read_bytes()).hexdigest()
    store = MemoryStore()
    for row in records.values(): await store.upsert("museum_sources", row)
    base = MuseumVisualIndex(cfg.museum_visual_manifest, cfg.museum_visual_model, records, cache_dir=cfg.museum_visual_cache)
    await base.start()
    added = MuseumVisualIndex(NEW_MANIFEST, cfg.museum_visual_model, records, encoder=base.encoder,
        cache_dir=cfg.museum_visual_cache, cache_namespace=base.cache_namespace)
    await added.start()
    assert base.label_required_ids == added.label_required_ids
    pack = ROOT / "data/private/MUSE-test-pack-12-20261006"
    cases = json.loads((pack / "manifest.json").read_bytes())
    new_raw = (NEW_MANIFEST.parent / next(r["path"] for r in json.loads(NEW_MANIFEST.read_bytes())["references"] if r["id"] == REFERENCE_ID)).read_bytes()
    normalized_new_hash = hashlib.sha256(prepare_image(new_raw)).hexdigest()
    offline = dict(corpus_hash=corpus_hash, baseline_index_hash=base.index_hash, added_index_hash=added.index_hash,
                   added_reference=REFERENCE_ID, cases=[], scope="known developer retrieval diagnosis, not recognition accuracy")
    raw_by_case = {}
    for n, case in enumerate(cases, 1):
        raw = (pack / case["file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == case["sha256"]
        clean = prepare_image(raw)
        assert hashlib.sha256(clean).hexdigest() != normalized_new_hash, "Reference/test exact duplicate"
        before, after = await base.search(clean, len(records)), await added.search(clean, len(records))
        target = case["expected_source_id"]
        rank = lambda hits: next((i for i, h in enumerate(hits, 1) if h["source_id"] == target), None)
        # Freeze identities/ranks/scores; only select each same work's best available reference.
        after_by_id = {h["source_id"]: h for h in after}
        selected = [{**h, "reference_id": after_by_id[h["source_id"]]["reference_id"]} for h in before[:3]]
        assert all(a["reference_id"] == b["reference_id"] for a, b in zip(before[:3], selected)
                   if a["source_id"] != "va-neptune-triton")
        offline["cases"].append(dict(case=n, expected=target, before_rank=rank(before), after_rank=rank(after),
                                    before=before[:3], after=after[:3], controlled_after=selected))
        raw_by_case[n] = raw
        print("retrieval", n, rank(before), "->", rank(after), [h["reference_id"] for h in selected], flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "offline.json"
    if p.exists(): assert json.loads(p.read_bytes()) == offline, "Baseline changed"
    else: p.write_text(json.dumps(offline, indent=2), encoding="utf-8")
    if not live: return

    class Index:
        async def search(self, query): return []
    index = Index(); index.records = records; index.corpus_hash = corpus_hash

    class FrozenVisual:
        def __init__(self, real, hits):
            self.real, self.hits = real, hits
            self.index_hash, self.label_required_ids = real.index_hash, real.label_required_ids
        async def search(self, raw): return self.hits
        def reference_image(self, hit): return self.real.reference_image(hit)

    report = dict(scope="single-reference controlled diagnostic; fixed visual Top3 IDs/scores, not online AB",
                  corpus_hash=corpus_hash, baseline_index_hash=base.index_hash, added_index_hash=added.index_hash,
                  model=cfg.deepseek_model, prompt_version=VERSION, cases=list(LIVE_CASES), model_requests=0,
                  max_model_requests=35, results=[])
    for n in LIVE_CASES:
        row = offline["cases"][n - 1]; observation = {}
        engine = MuseumEngine(cfg, store, index)
        engine.settings = cfg.model_copy(update={"museum_photo_verification": "visibility", "museum_photo_reference_mode": "single"})
        for repeat in range(3):
            modes = ["before", "added"] if repeat % 2 == 0 else ["added", "before"]
            for mode in modes:
                real = engine._client()
                class ReplayObservation:
                    @property
                    def usage_records(self): return real.usage_records
                    async def complete_json(self, messages):
                        observe = messages[0]["content"].startswith("只描述照片")
                        if observe and "value" in observation: return observation["value"]
                        assert report["model_requests"] < 35
                        report["model_requests"] += 1
                        value = await real.complete_json(messages)
                        if observe: observation["value"] = value
                        return value
                engine.client_factory = ReplayObservation
                engine.visual_index = FrozenVisual(base, row["before"]) if mode == "before" else FrozenVisual(added, row["controlled_after"])
                started = time.perf_counter()
                result = await PhotoRecognizer(engine).recognize(raw_by_case[n], {"_id": f"view-{n}-{repeat}-{mode}"})
                trace = await store.get("museum_photo_traces", result["trace_id"])
                report["results"].append(dict(case=n, repeat=repeat, mode=mode, expected=row["expected"], result=result,
                                             trace=trace, ms=round((time.perf_counter() - started) * 1000)))
                (OUT / "live.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                print(n, repeat, mode, result["status"], trace["candidate_ids"], trace["error"], flush=True)
                if "value" not in observation:
                    raise RuntimeError("Observation failed; stopped without retry")
    report["complete"] = True
    report["outcomes"] = {m: dict(Counter(classify(r) for r in report["results"] if r["mode"] == m)) for m in ("before", "added")}
    (OUT / "live.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(requests=report["model_requests"], outcomes=report["outcomes"])))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main(args.live))
