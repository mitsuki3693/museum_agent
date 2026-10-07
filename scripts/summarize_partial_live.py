"""Summarize a completed private photo experiment without treating repeats as users."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "eval/private"
MODES = ("legacy", "visibility", "candidate", "partial")

def classify(row):
    trace, expected = row["trace"], row["expected"]
    if trace.get("error") or row["result"]["status"] == "service_unavailable":
        return "execution_error"
    candidates = trace["candidate_ids"]
    if any(sid != expected for sid in candidates):
        return "wrong_identity_candidate"
    if expected is None:
        return "ood_no_identity"
    if expected in candidates:
        return "correct_confirmation_candidate"
    return "in_gallery_no_identity"

def percentile(values, p):
    values = sorted(values)
    if not values:
        return None
    index = (len(values)-1)*p
    low = int(index)
    high = min(low+1, len(values)-1)
    return round(values[low]+(values[high]-values[low])*(index-low), 1)

def summarize(report):
    rows = report["results"]
    groups = {}
    for mode in MODES:
        chosen = [r for r in rows if r["mode"] == mode]
        cases = {}
        for case in sorted({r["case"] for r in chosen}):
            items = [r for r in chosen if r["case"] == case]
            cases[str(case)] = dict(outcomes=dict(Counter(classify(r) for r in items)),
                repeats=[dict(repeat=r["repeat"],outcome=classify(r),state=r["result"]["match_state"],
                              candidate_ids=r["trace"]["candidate_ids"],similar_ids=r["trace"]["similar_candidate_ids"],
                              error=r["trace"].get("error"),error_cause=r["trace"].get("error_cause")) for r in items])
        usage = [u for r in chosen for u in r["trace"].get("usage", [])]
        # Unknown usage is never converted to free API calls. Provider accounting
        # covers returned usage records only; failed requests may still cost money.
        groups[mode] = dict(runs=len(chosen), outcomes=dict(Counter(classify(r) for r in chosen)),cases=cases,
            latency_ms=dict(p50=percentile([r["ms"] for r in chosen],.5),p95=percentile([r["ms"] for r in chosen],.95)),
            errors=dict(Counter(r["trace"].get("error") for r in chosen if r["trace"].get("error"))),
            provider_usage_records=len(usage), known_tokens={k:sum(u.get(k,0) for u in usage) for k in
                ["prompt_tokens","completion_tokens","total_tokens","prompt_cache_hit_tokens","prompt_cache_miss_tokens"]})
    # Validate candidate and reference provenance independently of the outcomes.
    controlled = True
    for case in {r["case"] for r in rows}:
        traces=[r["trace"] for r in rows if r["case"]==case and r["trace"]["compared_ids"]]
        if traces:
            controlled &= all(t["compared_ids"]==traces[0]["compared_ids"] and t["visual_scores"]==traces[0]["visual_scores"] for t in traces)
    return dict(scope="six known developer photos, three repeats; not 72 independent samples or online AB",
                report_complete=report.get("complete",False),candidate_control_verified=bool(controlled),model=report["model"],
                modes=groups,total_runs=len(rows),max_authorized_model_requests=150,
                billing="No currency estimate; provider usage only, unknown failed-request usage is not zero",
                latency_boundary="Includes local retrieval, verification, and one observation call per photo in the first arm; small sample, not production P95")

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",default="partial-regions-v1/live.json")
    parser.add_argument("--output",default="partial-regions-v1/summary.json")
    args=parser.parse_args()
    source,dest=PRIVATE/args.input,PRIVATE/args.output
    if any(not p.resolve().is_relative_to(PRIVATE.resolve()) for p in [source,dest]):
        raise ValueError("Private evaluation paths only")
    if dest.exists():raise ValueError("Do not overwrite summaries")
    report=json.loads(source.read_bytes())
    if not report.get("complete"):raise ValueError("Report is not complete")
    summary=summarize(report)
    summary["report_sha256"]=hashlib.sha256(source.read_bytes()).hexdigest()
    dest.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"total_runs":summary["total_runs"],
        "candidate_control_verified":summary["candidate_control_verified"],
        "outcomes":{k:v["outcomes"] for k,v in summary["modes"].items()}},ensure_ascii=True,indent=2))

if __name__=="__main__":main()
