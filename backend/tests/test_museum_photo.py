import io
import json
from types import SimpleNamespace
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from app.museum.vision import prepare_image, PhotoRecognizer, MAX_UPLOAD_BYTES
from app.museum.api import create_app
from app.museum.config import MuseumSettings
from app.storage.store import MemoryStore

def image_bytes():
    image = Image.new("RGB", (32, 24), "blue")
    exif = Image.Exif()
    exif[270] = "private test description"
    out = io.BytesIO()
    image.save(out, format="JPEG", exif=exif)
    return out.getvalue()

def test_image_metadata_removed():
    raw = prepare_image(image_bytes())
    assert b"private test description" not in raw
    with Image.open(io.BytesIO(raw)) as img:
        assert img.format == "JPEG" and not img.getexif()

@pytest.mark.parametrize("raw", [b"", b"<svg>not a raster image</svg>", b"a" * (MAX_UPLOAD_BYTES + 1)], ids=["empty", "svg", "over_limit"])
def test_invalid_and_oversize_images_rejected(raw):
    with pytest.raises(ValueError):
        prepare_image(raw)

class FakeClient:
    def __init__(self, ids):
        self.results=iter([{"usable":True,"visible_text":"C.615-1925","visual_description":"Blue vase"},
            {"comparisons":[{"candidate_id":i,"identity":"same_work","features":[],"shared_features":["blue_white"],"needs":["label"]} for i in ids]}])
    async def complete_json(self, messages):
        return next(self.results)

class Index:
    async def search(self, query):
        return [{"_id":"test-1","title":"Test Vase","source_url":"https://example.org/1","content":"Blue vase","fields":{"accession_number":"C.615-1925"}}]

@pytest.mark.asyncio
async def test_match_needs_confirmation_and_no_photo_saved():
    store=MemoryStore()
    engine=SimpleNamespace(store=store,index=Index(),client_factory=lambda:FakeClient(["test-1"]))
    result=await PhotoRecognizer(engine).recognize(image_bytes(),{"_id":"session-1"})
    assert result["status"]=="needs_confirmation" and result["confirmation_required"]
    assert "answer" not in result
    trace=await store.get("museum_photo_traces",result["trace_id"])
    assert trace["session_id"]=="session-1" and "photo_hash" in trace
    assert "data:image" not in json.dumps(trace) and "visible_text" not in trace

@pytest.mark.asyncio
@pytest.mark.parametrize("ids,status",[([],"not_matched"),(["invented-object"],"service_unavailable")])
async def test_unknown_objects_never_become_matches(ids,status):
    engine=SimpleNamespace(store=MemoryStore(),index=Index(),client_factory=lambda:FakeClient(ids))
    result=await PhotoRecognizer(engine).recognize(image_bytes(),{"_id":"s"})
    assert result["status"]==status and result["candidates"]==[]

@pytest.mark.asyncio
async def test_provider_failure_records_stage_and_cause_without_error_body():
    from app.llm.client import LLMError
    class BrokenClient:
        async def complete_json(self,messages):
            raise LLMError("private provider response must not be logged") from TimeoutError()
    store=MemoryStore()
    engine=SimpleNamespace(store=store,index=Index(),client_factory=BrokenClient)
    result=await PhotoRecognizer(engine).recognize(image_bytes(),{"_id":"s"})
    trace=await store.get("museum_photo_traces",result["trace_id"])
    assert result["status"]=="service_unavailable"
    assert trace["last_stage"]=="observe_image" and trace["error_cause"]=="TimeoutError"
    assert "private provider response" not in json.dumps(trace)

def test_no_key_does_not_pretend_photo_was_recognized(tmp_path):
    corpus=tmp_path/'corpus.json'
    corpus.write_text(json.dumps([{"_id":"test-1","title":"Vase","content":"Blue vase","status":"active","source_hash":"test","source_url":"https://example.org","license":"test","fetched_at":"2026-09-29"}]),encoding='utf-8')
    config=MuseumSettings(_env_file=None,museum_corpus=corpus,museum_embedding='lexical',museum_storage='memory',deepseek_api_key='')
    with TestClient(create_app(config)) as client:
        token=client.post('/api/museum/sessions').json()['token']
        response=client.post('/api/museum/recognize',headers={'Authorization':'Bearer '+token},files={'photo':('test.jpg',image_bytes(),'image/jpeg')})
        assert response.status_code==503
