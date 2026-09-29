from __future__ import annotations
import asyncio
import hashlib
import hmac
import json
import secrets
import time
from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID
from fastapi import Depends, FastAPI, Header, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from app.config import Settings
from app.storage.store import MemoryStore, MongoStore
from app.storage.mongodb import MongoDB
from .config import MuseumSettings
from .retrieval import MuseumIndex
from .engine import MuseumEngine
from .vision import PhotoRecognizer, MAX_UPLOAD_BYTES
from .routes import RoutePlanner, RoutePreferences, RouteService, is_route_question

class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=600)
    mode: Literal["brief", "deep", "children"] = "brief"
    action: Literal["question", "narration", "route"] = "question"
    route: RoutePreferences | None = None
    object_id: str | None = Field(default=None, max_length=100)
    request_id: UUID

class FeedbackRequest(BaseModel):
    trace_id: str = Field(min_length=1, max_length=64)
    kind: Literal["helpful", "wrong_fact", "not_answered"]
    comment: str = Field(default="", max_length=500)

def create_app(settings: MuseumSettings | None = None, client_factory=None):
    config = settings or MuseumSettings()
    locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def lifespan(app):
        mongo = None
        if config.museum_storage == "mongo":
            mongo = MongoDB(Settings(_env_file=None, storage_mode="mongo", mongodb_uri=config.mongodb_uri,
                                     mongodb_db=config.mongodb_db))
            await asyncio.wait_for(mongo.connect(), 10)
            store = MongoStore(mongo)
        else:
            store = MemoryStore()
        index = MuseumIndex(config, store)
        await index.start()
        app.state.store, app.state.index = store, index
        app.state.engine = MuseumEngine(config, store, index, client_factory)
        app.state.routes = RouteService(RoutePlanner(config.museum_route_manifest), app.state.engine)
        if config.museum_visual_manifest:
            from .visual_index import MuseumVisualIndex
            visual = MuseumVisualIndex(config.museum_visual_manifest, config.museum_visual_model, index.records)
            await visual.start()
            app.state.engine.visual_index = visual
        app.state.slots = asyncio.Semaphore(config.museum_max_inflight)
        app.state.new_sessions = {}
        yield
        if mongo:
            await mongo.close()

    app = FastAPI(title="馆语 · 博物馆可信问答", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
                       allow_methods=["GET", "POST", "DELETE"], allow_headers=["Authorization", "Content-Type"])

    async def session(authorization: str = Header(default="")):
        if not authorization.startswith("Bearer ") or len(authorization) > 150:
            raise HTTPException(401, "请重新开始会话")
        sid = hashlib.sha256(authorization[7:].encode()).hexdigest()
        row = await app.state.store.get("museum_sessions", sid)
        if not row or row["expires_at"] < time.time():
            raise HTTPException(401, "会话已过期，请重新开始")
        return row

    async def admin(authorization: str = Header(default="")):
        if not config.museum_admin_token or not hmac.compare_digest(authorization, "Bearer " + config.museum_admin_token):
            raise HTTPException(403, "管理入口未启用或凭据无效")

    @app.get("/api/museum/health")
    async def health():
        return {"status": "ok", "model_configured": bool(config.deepseek_api_key),
                "storage": config.museum_storage, "retrieval": config.museum_embedding,
                "corpus_count": len(app.state.index.records), "corpus_hash": app.state.index.corpus_hash,
                "prompt_version": MuseumEngine.PROMPT_VERSION,
                "route_planning": app.state.routes.planner.unavailable() is None,
                "photo_retrieval": "image_and_text" if getattr(app.state.engine, "visual_index", None) else "caption_text",
                "visual_index_hash": getattr(getattr(app.state.engine, "visual_index", None), "index_hash", None)}

    @app.get("/api/museum/routes/options")
    async def route_options():
        return app.state.routes.planner.options()

    @app.get("/api/museum/objects")
    async def objects():
        image_file = config.museum_corpus.parent / "sample-images.json"
        images = {}
        if image_file.exists():
            for image in json.loads(image_file.read_text(encoding="utf-8-sig")).get("data", []):
                if image.get("is_public_domain") is True and image.get("image_id"):
                    key = f'artic-{image["id"]}'
                    images[key] = f'/collection/{key}.jpg'
        return [{"id": r["_id"], "title": r["title"], "source_url": r["source_url"],
                 "display_title": r.get("display_title"), "collection": r.get("collection", "Art Institute of Chicago"),
                 "has_narration": bool(r.get("narrations")), "source_kind": r.get("source_kind", "collection_record"),
                 "image_url": f'/api/museum/objects/{r["_id"]}/image' if r.get("local_image") else images.get(r["_id"])}
                for r in app.state.index.records.values()]

    @app.get("/api/museum/objects/{object_id}/image")
    async def object_image(object_id: str):
        record = app.state.index.records.get(object_id, {})
        if not config.museum_private_corpus or not record.get("local_image"):
            raise HTTPException(404, "暂无图片")
        root = config.museum_private_corpus.parent.resolve()
        path = (root / record["local_image"]).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in {".jpg", ".png", ".webp"}:
            raise HTTPException(404, "暂无图片")
        return FileResponse(path, headers={"Cache-Control": "private, max-age=300"})

    @app.post("/api/museum/recognize")
    async def recognize(photo: UploadFile = File(...), current=Depends(session)):
        if not config.deepseek_api_key:
            raise HTTPException(503, "照片识别尚未启用，请先选择示例作品")
        raw = await photo.read(MAX_UPLOAD_BYTES + 1)
        await photo.close()
        lock = locks.setdefault(current["_id"], asyncio.Lock())
        if lock.locked():
            raise HTTPException(409, "上一个问题仍在处理，请稍后")
        async with lock:
            if app.state.slots.locked():
                raise HTTPException(429, "服务繁忙，请稍后重试")
            async with app.state.slots:
                try:
                    return await asyncio.wait_for(PhotoRecognizer(app.state.engine).recognize(raw, current), 65)
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from None
                except asyncio.TimeoutError:
                    raise HTTPException(504, "照片识别超时，请换一张照片重试") from None

    @app.post("/api/museum/sessions")
    async def new_session(request: Request):
        now = time.time()
        # Single-process local demo limits. Do not advertise distributed rate limiting.
        addr = request.client.host if request.client else "local"
        buckets = app.state.new_sessions
        for key in list(buckets):
            if now - buckets[key][0] > 60:
                buckets.pop(key)
        bucket = buckets.setdefault(addr, [now, 0])
        if bucket[1] >= 20:
            raise HTTPException(429, "创建会话过于频繁，请稍后再试")
        bucket[1] += 1
        store = app.state.store
        for row in await store.find("museum_sessions"):
            if row["expires_at"] < now and not locks.get(row["_id"], asyncio.Lock()).locked():
                await store.delete("museum_sessions", row["_id"])
                locks.pop(row["_id"], None)
        if await store.count("museum_sessions") >= 200:
            raise HTTPException(503, "演示服务会话已满，请稍后再试")
        token = secrets.token_urlsafe(32)
        sid = hashlib.sha256(token.encode()).hexdigest()
        await store.upsert("museum_sessions", {"_id": sid, "history": [], "expires_at": now + config.museum_session_ttl})
        return {"token": token, "expires_in": config.museum_session_ttl}

    @app.post("/api/museum/chat")
    async def chat(body: ChatRequest, current=Depends(session)):
        if not body.query.strip():
            raise HTTPException(422, "请输入问题")
        if body.object_id and body.object_id not in app.state.index.records:
            raise HTTPException(422, "藏品不在当前资料范围内")
        if body.action == "narration" and not body.object_id:
            raise HTTPException(422, "请先确认要讲解的作品")
        if body.route is not None and body.action != "route":
            raise HTTPException(422, "路线偏好只能用于路线请求")
        route_request = body.action == "route" or (body.action == "question" and is_route_question(body.query))
        if body.route and not app.state.routes.planner.unavailable():
            try:
                app.state.routes.planner.validate_preferences(body.route)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
        lock = locks.setdefault(current["_id"], asyncio.Lock())
        if lock.locked():
            raise HTTPException(409, "上一个问题仍在处理，请等待完成")
        fingerprint = hashlib.sha256(body.model_dump_json(exclude={"request_id"}).encode()).hexdigest()
        cache_id = current["_id"] + ":" + str(body.request_id)
        async with lock:
            cached = await app.state.store.get("museum_requests", cache_id)
            if cached:
                if cached["fingerprint"] != fingerprint:
                    raise HTTPException(409, "相同请求编号不能用于不同问题")
                return cached["result"]
            if app.state.slots.locked():
                raise HTTPException(429, "服务正在处理其他问题，请稍后重试")
            # Re-read inside the lock; prevents overwriting history from a stale snapshot.
            current = await app.state.store.get("museum_sessions", current["_id"])
            async with app.state.slots:
                try:
                    if route_request:
                        work = app.state.routes.handle(body.query.strip(), current, body.object_id, body.route)
                    else:
                        handler = app.state.engine.narrate if body.action == "narration" else app.state.engine.answer
                        work = handler(body.query.strip(), current, body.mode, body.object_id)
                    result = await asyncio.wait_for(work, timeout=config.museum_timeout)
                except asyncio.TimeoutError:
                    raise HTTPException(504, "回答超时，请稍后重试") from None
            await app.state.store.upsert("museum_sessions", current)
            await app.state.store.upsert("museum_requests", {"_id": cache_id, "fingerprint": fingerprint, "result": result})
            return result

    @app.post("/api/museum/feedback")
    async def feedback(body: FeedbackRequest, current=Depends(session)):
        trace = await app.state.store.get("museum_traces", body.trace_id)
        if not trace or trace["session_id"] != current["_id"]:
            raise HTTPException(404, "未找到本会话的回答")
        await app.state.store.upsert("museum_feedback", {"_id": body.trace_id, "session_id": current["_id"],
            **body.model_dump(), "updated_at": time.time(), "review_status": "pending"})
        return {"saved": True}

    @app.delete("/api/museum/session")
    async def delete_session(current=Depends(session)):
        lock = locks.setdefault(current["_id"], asyncio.Lock())
        if lock.locked():
            raise HTTPException(409, "请等待当前回答结束后清除")
        async with lock:
            for table in ["museum_traces", "museum_feedback", "museum_photo_traces"]:
                for row in await app.state.store.find(table, {"session_id": current["_id"]}):
                    await app.state.store.delete(table, row["_id"])
            for row in await app.state.store.find("museum_requests"):
                if row["_id"].startswith(current["_id"] + ":"):
                    await app.state.store.delete("museum_requests", row["_id"])
            await app.state.store.delete("museum_sessions", current["_id"])
        locks.pop(current["_id"], None)
        return {"deleted": True}

    @app.get("/api/museum/admin/traces", dependencies=[Depends(admin)])
    async def traces():
        return {"traces": await app.state.store.find("museum_traces", limit=100),
                "feedback": await app.state.store.find("museum_feedback", limit=100),
                "photo_traces": await app.state.store.find("museum_photo_traces", limit=100)}

    return app

app = create_app()
