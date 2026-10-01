"""Versioned evaluation CLI; raw reports stay local. Never auto-approve human labels."""
import argparse
import asyncio
import hashlib
import json
import time
import uuid
from pathlib import Path

import httpx
import numpy as np
from app.museum.config import MuseumSettings, ROOT
from app.museum.engine import MuseumEngine
from app.museum.retrieval import MuseumIndex
from app.museum.visual_index import MuseumVisualIndex
from app.museum.vision import PhotoRecognizer
from app.museum.evaluation import freeze_dataset, validate_frozen, retrieval_metrics, usage_cost
from app.storage.store import MemoryStore


async def run(args):
    settings = MuseumSettings()
    store = MemoryStore()
    index = MuseumIndex(settings, store)
    await index.start()
    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    refs = json.loads(settings.museum_visual_manifest.read_text(encoding="utf-8")) if settings.museum_visual_manifest else None
    out = Path(args.output)
    if out.exists():
        raise ValueError("Refusing to overwrite an evidence file")
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.command == "freeze":
        report = freeze_dataset(dataset, index, references=refs, root=ROOT)
    else:
        if args.draft_smoke:
            if args.answers or dataset["kind"] != "text":
                raise ValueError("Unreviewed smoke is text retrieval only; no paid calls")
        else:
            validate_frozen(dataset, index.corpus_hash)
        if (args.answers or dataset["kind"] == "photo") and not settings.deepseek_api_key:
            raise ValueError("Model credentials required for live evaluation")
        pricing = json.loads(Path(args.pricing).read_text()) if args.pricing else None
        report = {"_id": uuid.uuid4().hex, "created_at": time.time(), "kind": dataset["kind"],
                  "status": "running", "dataset_version": dataset["version"],
                  "dataset_hash": dataset.get("dataset_hash") or hashlib.sha256(Path(args.dataset).read_bytes()).hexdigest(),
                  "corpus_hash": index.corpus_hash, "model": settings.deepseek_model,
                  "prompt_version": MuseumEngine.PROMPT_VERSION, "embedding_model": settings.museum_embedding_model,
                  "human_reviewed": not args.draft_smoke, "online_ab": False, "pricing": pricing, "results": []}
        engine = MuseumEngine(settings, store, index)
        visual = None
        if dataset["kind"] == "photo":
            current_refs = hashlib.sha256(json.dumps(refs, sort_keys=True).encode()).hexdigest()
            if current_refs != dataset["reference_manifest_hash"]:
                raise ValueError("Reference library changed since freeze")
            visual = MuseumVisualIndex(settings.museum_visual_manifest, settings.museum_visual_model, index.records)
            await visual.start()
            engine.visual_index = visual
            report.update(visual_index_hash=visual.index_hash, photo_prompt_version=PhotoRecognizer.PROMPT_VERSION)
        for case in dataset["cases"][:args.max_cases]:
            for variant in (["bm25", "hybrid"] if dataset["kind"] == "text" else ["image_and_text"]):
                started = time.perf_counter()
                row = {"case_id": case["id"], "category": case["category"], "variant": variant, "human_grade": None}
                if dataset["kind"] == "text":
                    # No selected-object boost in retrieval metrics. Followup metrics need the actual rewrite.
                    if case["category"] == "followup":
                        row.update(recall_at_1=None, recall_at_5=None, mrr=None)
                    else:
                        hits = await index.search(case["question"], None, variant)
                        row["retrieved_ids"] = [h["_id"] for h in hits]
                        row.update(retrieval_metrics(row["retrieved_ids"], case["gold_source_ids"]))
                    if args.answers:
                        session = {"_id": report["_id"] + case["id"] + variant, "history": case.get("history", [])}
                        # Entity provided only for an explicit style task, never a retrieval question.
                        selected = case["gold_source_ids"][0] if case["category"] == "style" else None
                        result = await engine.answer(case["question"], session, case.get("mode", "brief"), selected, variant)
                        row["result"] = result
                        if case["category"] == "followup":
                            row.update(retrieval_metrics(result["retrieved_ids"], case["gold_source_ids"]))
                        row.update(usage_cost(result.get("usage", []), pricing))
                        row["expected_refusal"] = case["should_refuse"]
                else:
                    raw = (ROOT / case["path"]).read_bytes()
                    if hashlib.sha256(raw).hexdigest() != case["sha256"]:
                        raise ValueError("Photo changed after freeze")
                    result = await PhotoRecognizer(engine).recognize(raw, {"_id": report["_id"] + case["id"]})
                    trace = await store.get("museum_photo_traces", result["trace_id"])
                    ids, gold = trace["visual_retrieved_ids"], case["gold_source_ids"]
                    candidates = [c["id"] for c in result["candidates"]]
                    row.update(result=result, visual_top1=bool(set(ids[:1]) & set(gold)) if gold else None,
                               visual_top3=bool(set(ids[:3]) & set(gold)) if gold else None,
                               wrong_candidate=bool(set(candidates) - set(gold)),
                               confirmed_candidate=bool(set(candidates) & set(gold)),
                               rejected=result["status"] == "not_matched", service_error=result["status"] == "service_unavailable",
                               visible_text_present=trace.get("visible_text_present"), text_retrieved_ids=trace["text_retrieved_ids"],
                               reshoot_count=None)
                    row.update(usage_cost(trace.get("usage", []), pricing))
                row["latency_ms"] = round((time.perf_counter() - started) * 1000)
                report["results"].append(row)
                out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["status"] = "completed"
        report["summary"] = {}
        for variant in {r["variant"] for r in report["results"]}:
            rows = [r for r in report["results"] if r["variant"] == variant]
            summary = {"cases": len(rows), "human_grades_completed": 0,
                       "p50_ms": float(np.percentile([r["latency_ms"] for r in rows], 50)),
                       "p95_ms": float(np.percentile([r["latency_ms"] for r in rows], 95))}
            for key in ("recall_at_1", "recall_at_5", "mrr"):
                values = [r[key] for r in rows if r.get(key) is not None]
                summary[key] = sum(values)/len(values) if values else None
            for key in ("wrong_candidate", "confirmed_candidate", "rejected", "service_error"):
                summary[key] = sum(bool(r.get(key)) for r in rows) if dataset["kind"] == "photo" else None
            report["summary"][variant] = summary
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.persist:
            if not settings.museum_admin_token:
                raise ValueError("Admin credential required to persist evaluation run")
            # Persist compact metrics, not copyrighted source passages or raw model transcripts.
            compact = {k: v for k, v in report.items() if k != "results"}
            compact["results"] = [{k: v for k, v in r.items() if k != "result"} for r in report["results"]]
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post("http://127.0.0.1:8000/api/museum/admin/eval-runs",
                    headers={"Authorization": "Bearer " + settings.museum_admin_token}, json=compact)
                response.raise_for_status()
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(out), "status": report.get("status", "frozen"), "human_reviewed": report.get("human_reviewed")}, ensure_ascii=False))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["freeze", "run"])
    p.add_argument("--dataset", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--draft-smoke", action="store_true")
    p.add_argument("--answers", action="store_true")
    p.add_argument("--persist", action="store_true")
    p.add_argument("--pricing")
    p.add_argument("--max-cases", type=int, choices=range(1, 41), default=40)
    asyncio.run(run(p.parse_args()))
