"""Synthetic reproduction of the observed part/literal_error failure class.

The earlier live traces did not retain rejected values. 'body' is an illustrative
unknown label, not a claimed replay of the provider's exact failed response.
"""
from types import SimpleNamespace
import json
import pytest
from app.museum.vision import PhotoRecognizer
from app.storage.store import MemoryStore
from .test_museum_visual_retrieval import image_bytes


async def run_photo(features, mode="visibility", label_gate=False):
    source = dict(_id="flower", title="Flower", status="active", source_hash="fixture")
    store = MemoryStore()
    await store.upsert("museum_sources", source)

    class Index:
        records = {"flower": source}
        async def search(self, query): return []

    class Visual:
        label_required_ids = {"flower"} if label_gate else set()
        async def search(self, raw):
            return [dict(source_id="flower", reference_id="front", score=.8)]
        def reference_image(self, hit): return image_bytes()

    class Client:
        calls = 0
        async def complete_json(self, messages):
            self.calls += 1
            if messages[0]["content"].startswith("只描述照片"):
                return dict(usable=True, visible_text="", visual_description="flower")
            return {"comparisons": [dict(candidate_id="flower", identity="same_work", features=features)]}

    client = Client()
    engine = SimpleNamespace(store=store, index=Index(), visual_index=Visual(), client_factory=lambda: client,
                             settings=SimpleNamespace(museum_photo_verification=mode))
    result = await PhotoRecognizer(engine).recognize(image_bytes(), {"_id": "part-contract-test"})
    trace = await store.get("museum_photo_traces", result["trace_id"])
    assert client.calls == 2  # No repair/retry model call.
    return result, trace


def feature(part, relation="visible_match", visible=True):
    return dict(part=part, query_detail="fixture query detail", reference_detail="fixture reference detail",
                relation=relation, distinctive=True, query_visible=visible, reference_visible=True)


@pytest.mark.asyncio
async def test_unknown_part_does_not_crash_or_become_second_identity_feature():
    result, trace = await run_photo([feature("top"), feature("body")])
    assert result["status"] == "needs_confirmation"
    assert result["match_state"] == "uncertain"
    assert trace["comparison_summary"][0]["matching_parts"] == ["top"]
    assert trace["error"] is None
    assert trace["visibility_summary"][0]["regions"][1]["part_status"] == "unsupported"
    assert "body" not in json.dumps(trace)


@pytest.mark.asyncio
async def test_unknown_part_conflict_cannot_be_discarded():
    result, trace = await run_photo([feature("top"), feature("base"), feature("body", "visible_conflict")])
    assert result["status"] == "not_matched"
    assert not result["candidates"]
    assert trace["error"] is None


@pytest.mark.asyncio
async def test_unknown_unseen_region_is_not_a_veto_and_label_gate_survives():
    features = [feature("top"), feature("body", "visible_conflict", visible=False)]
    result, _ = await run_photo(features)
    assert result["match_state"] == "uncertain"
    result, trace = await run_photo(features, label_gate=True)
    assert not result["candidates"]
    assert trace["comparison_summary"][0]["reason"] == "lookalike_requires_label"


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [None, 123, [], "", "x" * 41])
async def test_malformed_part_remains_a_schema_failure(invalid):
    result, trace = await run_photo([feature("top"), feature(invalid)])
    assert result["status"] == "service_unavailable"
    assert trace["error"] == "ValidationError"


@pytest.mark.asyncio
async def test_unknown_relation_cannot_be_repaired_into_a_match():
    result, trace = await run_photo([feature("top"), feature("body", "surely_same")])
    assert result["status"] == "service_unavailable"
    assert trace["error"] == "ValidationError"


@pytest.mark.asyncio
async def test_only_unknown_part_matches_cannot_support_identity():
    result, trace = await run_photo([feature("body"), feature("neck")])
    assert result["status"] == "not_matched"
    assert not result["candidates"]
    assert trace["error"] is None
