"""One paid development case; not a benchmark or human accuracy evaluation."""
import argparse
import asyncio
import json
import time
from app.museum.config import MuseumSettings, ROOT
from app.museum.engine import MuseumEngine
from app.museum.retrieval import MuseumIndex
from app.storage.store import MemoryStore


async def run():
    settings = MuseumSettings()
    if not settings.deepseek_api_key:
        raise SystemExit("Configure the local API key first.")
    store = MemoryStore()
    index = MuseumIndex(settings, store)
    await index.start()
    result = await MuseumEngine(settings, store, index).answer(
        "请介绍这件作品，让我先知道值得留意的地方。",
        {"_id": "local-live-smoke"}, "brief", "artic-28560")
    report = {
        "kind": "single live development smoke; not accuracy evaluation",
        "human_reviewed": False,
        "generated_at": time.time(),
        "thinking": "disabled", "max_tokens_per_call": 1800,
        "result": result, "traces": await store.find("museum_traces"),
    }
    path = ROOT / "eval/live-generation-smoke.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    print(json.dumps({"report": str(path), "status": result["status"],
                      "latency_ms": result["latency_ms"], "usage": result["usage"]}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Allow this small paid API smoke test")
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to make paid model calls; no calls made.")
    asyncio.run(run())
