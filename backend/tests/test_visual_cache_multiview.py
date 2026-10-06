"""Cache equivalence and bounded multiview evidence; no model-quality claims."""
import hashlib
import json
import numpy as np
import pytest
from PIL import Image
from types import SimpleNamespace
from app.museum.visual_index import MuseumVisualIndex

class Encoder:
    def __init__(self):
        self.calls = 0

    def encode(self, images):
        self.calls += 1
        a = np.array([np.asarray(im).mean(axis=(0, 1)) for im in images], dtype=np.float32)
        return a / np.linalg.norm(a, axis=1, keepdims=True)

def fixture(tmp_path):
    refs, records = [], {}
    for key, sid, color, view in [("whole", "one", "red", "whole"), ("detail", "one", "orange", "detail"),
                                  ("other", "two", "blue", "whole")]:
        path = tmp_path / (key + ".jpg")
        Image.new("RGB", (40, 60), color).save(path)
        records[sid] = {"_id": sid, "status": "active"}
        refs.append(dict(id=key, source_id=sid, view=view, path=path.name,
                         sha256=hashlib.sha256(path.read_bytes()).hexdigest(), source_url="https://example.org/" + key, license="test"))
    manifest = tmp_path / "refs.json"
    manifest.write_text(json.dumps(dict(version=1, references=refs)))
    return manifest, records

@pytest.mark.asyncio
async def test_cache_warm_start_preserves_ranking_and_rebuilds_damage(tmp_path):
    manifest, records = fixture(tmp_path)
    def create(namespace="test-v1"):
        return MuseumVisualIndex(manifest, tmp_path, records, encoder=Encoder(),
                                 cache_dir=tmp_path / "cache", cache_namespace=namespace)
    cold = create()
    await cold.start()
    assert cold.encoder.calls == 3
    query = (tmp_path / "whole.jpg").read_bytes()
    expected = await cold.search(query)
    warm = create()
    await warm.start()
    assert warm.encoder.calls == 0 and warm.cache_stats["hits"] == 3
    assert await warm.search(query) == expected
    cache_file = next((tmp_path / "cache").glob("*.npz"))
    cache_file.write_bytes(b"interrupted write")
    repaired = create()
    await repaired.start()
    assert repaired.encoder.calls == 1
    assert await repaired.search(query) == expected
    changed_model = create("test-v2")
    await changed_model.start()
    assert changed_model.encoder.calls == 3
    # An actual image change invalidates its feature even when its reference ID remains stable.
    Image.new("RGB", (40, 60), "green").save(tmp_path / "detail.jpg")
    spec = json.loads(manifest.read_bytes())
    spec["references"][1]["sha256"] = hashlib.sha256((tmp_path / "detail.jpg").read_bytes()).hexdigest()
    manifest.write_text(json.dumps(spec))
    changed_image = create()
    await changed_image.start()
    assert changed_image.encoder.calls == 1

@pytest.mark.asyncio
async def test_multiview_changes_only_reference_evidence_not_candidates_or_scores(tmp_path):
    manifest, records = fixture(tmp_path)
    index = MuseumVisualIndex(manifest, tmp_path, records, encoder=Encoder())
    await index.start()
    hits = await index.search((tmp_path / "whole.jpg").read_bytes())
    snapshot = json.dumps(hits)
    expanded = index.comparison_views(hits + hits)
    assert {h["source_id"] for h in expanded} == {h["source_id"] for h in hits}
    assert [h["reference_id"] for h in expanded] == ["whole", "detail", "other"]
    assert "score" not in expanded[1]  # Extra view isn't an independent retrieval vote.
    assert json.dumps(hits) == snapshot
    assert await index.search((tmp_path / "whole.jpg").read_bytes()) == hits

@pytest.mark.asyncio
async def test_read_only_cache_falls_back_to_encoding(tmp_path):
    manifest, records = fixture(tmp_path)
    blocked = tmp_path / "file-not-dir"
    blocked.write_text("not a cache directory")
    index = MuseumVisualIndex(manifest, tmp_path, records, encoder=Encoder(), cache_dir=blocked, cache_namespace="test")
    await index.start()
    assert index.cache_stats["write_errors"] == 3
    assert (await index.search((tmp_path / "whole.jpg").read_bytes()))[0]["source_id"] == "one"

@pytest.mark.asyncio
async def test_photo_pipeline_multiview_keeps_retrieval_order_and_private_export():
    from app.storage.store import MemoryStore
    from app.museum.vision import PhotoRecognizer
    from app.museum.runtime import export_metrics
    from .test_museum_visual_retrieval import image_bytes
    rows = [{"_id": key, "title": key, "status": "active", "source_hash": "v1"} for key in ["one", "two"]]
    store = MemoryStore()
    for row in rows:
        await store.upsert("museum_sources", row)
    class Text:
        records = {r["_id"]: r for r in rows}
        async def search(self, query):
            return rows
    class Visual:
        async def search(self, raw):
            return [dict(source_id="one", reference_id="one-front", score=.9),
                    dict(source_id="two", reference_id="two-front", score=.8)]
        def comparison_views(self, hits):
            return [hits[0], dict(source_id="one", reference_id="one-side"), hits[1]]
        def reference_image(self, hit):
            return image_bytes()
    class Client:
        async def complete_json(self, messages):
            if "观察这张照片" in str(messages):
                return dict(usable=True, visible_text="", visual_description="statue")
            assert sum(c["type"] == "image_url" for c in messages[1]["content"]) == 4
            return {"comparisons": [dict(candidate_id=key, identity="same_work", features=[
                dict(part=p, query_detail="matching detail", reference_detail="matching detail", relation="match", distinctive=True)
                for p in ["top", "base"]]) for key in ["two", "one"]]}
    engine = SimpleNamespace(store=store, index=Text(), visual_index=Visual(), client_factory=Client,
                             settings=SimpleNamespace(museum_photo_reference_mode="multiview"))
    result = await PhotoRecognizer(engine).recognize(image_bytes(), {"_id": "private-session"})
    assert [c["id"] for c in result["candidates"]] == ["one", "two"]
    trace = await store.get("museum_photo_traces", result["trace_id"])
    exported = export_metrics(dict(traces=[], photo_traces=[trace], feedback=[]))["rows"][0]
    assert exported["reference_mode"] == "multiview"
    assert exported["comparison_image_ids"] == ["one-front", "one-side", "two-front"]
    assert "data:image" not in json.dumps(exported) and "private-session" not in json.dumps(exported)
