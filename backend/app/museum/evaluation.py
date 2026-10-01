"""Freeze reviewed tasks before paid evaluation. Missing human labels remain missing."""
import hashlib
import json
from collections import Counter
from pathlib import Path

TEXT_QUOTAS = {"retrieval": 20, "followup": 8, "false_premise": 4, "unanswerable": 4, "style": 4}
PHOTO_QUOTAS = {"clear": 8, "detail": 6, "angle": 4, "screen_or_glare": 4, "label": 4, "outside": 4}


def freeze_dataset(dataset, index, *, references=None, root=Path(".")):
    kind, cases = dataset["kind"], dataset["cases"]
    quotas = TEXT_QUOTAS if kind == "text" else PHOTO_QUOTAS
    if kind not in ("text", "photo") or Counter(c["category"] for c in cases) != Counter(quotas):
        raise ValueError("Dataset category counts do not match the protocol")
    if len({c["id"] for c in cases}) != len(cases):
        raise ValueError("Duplicate case IDs")
    reference_hashes = {r["sha256"] for r in (references or {}).get("references", [])}
    for case in cases:
        if case.get("reviewed") is not True or not case.get("reviewer") or not case.get("reviewed_at"):
            raise ValueError("Human review, reviewer and review date required: " + case["id"])
        for sid in case["gold_source_ids"]:
            if sid not in index.records:
                raise ValueError("Unknown source: " + sid)
        if kind == "text":
            if type(case.get("should_refuse")) is not bool:
                raise ValueError("Explicit refusal label required")
            if not case.get("forbidden_facts") or not case.get("expected_behavior"):
                raise ValueError("Expected behavior and forbidden claims required")
            if case["gold_source_ids"] and (not case.get("evidence") or not case.get("allowed_facts")):
                raise ValueError("Evidence and allowed facts required")
            for evidence in case.get("evidence", []):
                source = index.records.get(evidence["source_id"])
                if not source or not evidence["quote"] or evidence["quote"] not in source["content"]:
                    raise ValueError("Evidence must occur verbatim in source")
        else:
            if not case.get("path") or not case.get("source_url"):
                raise ValueError("Photo path and source URL required")
            digest = hashlib.sha256((root / case["path"]).read_bytes()).hexdigest()
            if digest != case.get("sha256") or digest in reference_hashes:
                raise ValueError("Changed image or reference/test overlap")
            if case["category"] == "outside" and case["gold_source_ids"]:
                raise ValueError("Outside case cannot have an in-library identity")
            if case["category"] != "outside" and not case["gold_source_ids"]:
                raise ValueError("In-library photo must have a reviewed identity")
    result = {**dataset, "frozen": True, "corpus_hash": index.corpus_hash,
              "reference_manifest_hash": hashlib.sha256(json.dumps(references, sort_keys=True).encode()).hexdigest() if references else None}
    result["dataset_hash"] = hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return result


def validate_frozen(dataset, corpus_hash):
    content = {k: v for k, v in dataset.items() if k != "dataset_hash"}
    digest = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if not dataset.get("frozen") or digest != dataset.get("dataset_hash") or corpus_hash != dataset["corpus_hash"]:
        raise ValueError("Dataset or corpus changed; review and freeze a new version")


def retrieval_metrics(ids, gold):
    if not gold:
        return {"recall_at_1": None, "recall_at_5": None, "mrr": None}
    ranks = [ids.index(g) + 1 for g in set(gold) if g in ids]
    return {"recall_at_1": sum(r <= 1 for r in ranks) / len(set(gold)),
            "recall_at_5": sum(r <= 5 for r in ranks) / len(set(gold)),
            "mrr": 1 / min(ranks) if ranks else 0}


def usage_cost(usage, pricing=None):
    tokens = {k: sum(int(u.get(k, 0)) for u in usage) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
    # Unknown price is not zero cost. Input rates must include cache hit/miss differences.
    cost = None
    if pricing:
        if not pricing.get("source_url") or not pricing.get("as_of"):
            raise ValueError("Pricing source and date required")
        hit = sum(int(u.get("prompt_cache_hit_tokens", 0)) for u in usage)
        miss = tokens["prompt_tokens"] - hit
        cost = (hit * pricing["input_cached_per_million"] + miss * pricing["input_per_million"] + tokens["completion_tokens"] * pricing["output_per_million"]) / 1_000_000
    return {"tokens": tokens, "api_cost": cost, "currency": pricing.get("currency") if pricing else None}
