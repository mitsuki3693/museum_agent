"""Private, controlled developer regression; never reported as blind accuracy.

Compares gallery sizes using the same query vectors. --live additionally compares
single/multiview verification with one frozen observation and identical candidates.
Live API failures are retained; no automatic retry or success-only filtering.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import time
from app.museum.config import MuseumSettings
from app.museum.retrieval import MuseumIndex
from app.museum.visual_index import MuseumVisualIndex, DinoEncoder, MODEL_REVISION
from app.museum.engine import MuseumEngine
from app.museum.vision import PhotoRecognizer, prepare_image
from app.museum.photo_policy import POLICY_VERSION
from app.storage.store import MemoryStore

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "data/private"

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def write(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)

async def main(args):
    output = ROOT / "eval/private" / args.output
    if not output.resolve().is_relative_to((ROOT / "eval/private").resolve()) or output.exists():
        raise ValueError("Use a new output path under eval/private; never overwrite evidence")
    output.parent.mkdir(parents=True, exist_ok=True)
    pack = PRIVATE / "MUSE-test-pack-12-20261006"
    cases = json.loads((pack / "manifest.json").read_bytes())
    baseline = PRIVATE / "visual-references-v7-blue-release.json"
    expanded = PRIVATE / "va-pilot-100-v1-references.json"
    corpus = PRIVATE / "va-pilot-100-v1-corpus.json"
    cfg = MuseumSettings().model_copy(update={"museum_private_corpus": corpus, "museum_visual_manifest": expanded})
    store = MemoryStore()
    idx = MuseumIndex(cfg, store)
    await idx.start()
    encoder = DinoEncoder(cfg.museum_visual_model)
    indexes, starts = {}, {}
    for name, manifest in [("baseline", baseline), ("expanded", expanded), ("warm", expanded)]:
        t = time.perf_counter()
        visual = MuseumVisualIndex(manifest, cfg.museum_visual_model, idx.records, encoder=encoder,
                                   cache_dir=cfg.museum_visual_cache, cache_namespace=MODEL_REVISION)
        await visual.start()
        indexes[name] = visual
        starts[name] = dict(ms=round((time.perf_counter()-t)*1000), cache=visual.cache_stats,
                            image_count=len(visual.images), work_count=len(visual.references_by_source), hash=visual.index_hash)
        print(name, starts[name], flush=True)
    report = dict(scope="known developer regressions, not unseen accuracy or online A/B", model=cfg.deepseek_model,
                  prompt_version=PhotoRecognizer.PROMPT_VERSION, policy_version=POLICY_VERSION,
                  corpus_sha256=digest(corpus), corpus_hash=idx.corpus_hash, case_manifest_sha256=digest(pack / "manifest.json"),
                  starts=starts, retrieval=[], pairs=[])
    for n, case in enumerate(cases, 1):
        path = pack / case["file"]
        assert digest(path) == case["sha256"]
        clean = prepare_image(path.read_bytes())
        row = dict(case=n, expected=case["expected_source_id"], reference_overlap=case["in_reference_library"], results={})
        for name in ("baseline", "expanded"):
            t = time.perf_counter()
            hits = await indexes[name].search(clean, top_k=10)
            ids = [h["source_id"] for h in hits]
            expected = case["expected_source_id"]
            row["results"][name] = dict(rank=ids.index(expected)+1 if expected in ids else None, top10=hits,
                                        ms=round((time.perf_counter()-t)*1000))
        assert await indexes["expanded"].search(clean) == await indexes["warm"].search(clean)
        report["retrieval"].append(row)
        print("retrieval", n, {k:v["rank"] for k,v in row["results"].items()}, flush=True)
        write(output, report)
    if args.live:
        engine = MuseumEngine(cfg, store, idx)
        engine.visual_index = indexes["expanded"]
        for n in [1, 2, 4, 5, 6, 8, 11, 12]:
            case = cases[n-1]
            frozen = {}
            pair = dict(case=n, expected=case["expected_source_id"], runs=[])
            # Counterbalance mode order. First observation is reused verbatim for the pair.
            order = ["single", "multiview"] if n % 2 else ["multiview", "single"]
            for mode in order:
                real = engine._client()
                class ReplayObservation:
                    @property
                    def usage_records(self):
                        return real.usage_records
                    async def complete_json(self, messages):
                        observe = messages[0]["content"].startswith("只描述照片")
                        if observe and "value" in frozen:
                            return frozen["value"]
                        value = await real.complete_json(messages)
                        if observe:
                            frozen["value"] = value
                        return value
                engine.client_factory = ReplayObservation
                engine.settings = cfg.model_copy(update={"museum_photo_reference_mode": mode})
                result = await PhotoRecognizer(engine).recognize((pack / case["file"]).read_bytes(), {"_id": f"isolated-pilot-{n}-{mode}"})
                trace = await store.get("museum_photo_traces", result["trace_id"])
                pair["runs"].append(dict(mode=mode, result=result, trace=trace))
                print("comparison", n, mode, result["match_state"], [x["id"] for x in result["candidates"]], trace["error"], flush=True)
                if "value" not in frozen:
                    pair["incomplete_reason"] = "observation_failed; not retried"
                    break
            if len(pair["runs"]) == 2:
                a, b = [r["trace"] for r in pair["runs"]]
                assert a["compared_ids"] == b["compared_ids"] and a["visual_scores"] == b["visual_scores"]
                pair["same_reference_images"] = a["comparison_image_ids"] == b["comparison_image_ids"]
            pair["observation_sha256"] = hashlib.sha256(json.dumps(frozen, sort_keys=True).encode()).hexdigest()
            report["pairs"].append(pair)
            write(output, report)
    report["complete"] = True
    write(output, report)
    print("Saved private evaluation:", output.name, flush=True)

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="pilot-100-multiview-v1.json")
    parser.add_argument("--live", action="store_true", help="Up to 24 real model calls; API charges apply")
    asyncio.run(main(parser.parse_args()))
