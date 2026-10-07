from __future__ import annotations
import asyncio
import hashlib
import hmac
import json
import secrets
import time
from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID, uuid4
from fastapi import Depends, FastAPI, Header, HTTPException, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from app.config import Settings
from app.storage.store import MemoryStore, MongoStore
from .runtime import MuseumMongo, RuntimeStore, WriteGate, review_rows, export_metrics, FAILURE_STAGES
from .backup import BackupManager
from .config import MuseumSettings
from .retrieval import MuseumIndex
from .engine import MuseumEngine
from .vision import PhotoRecognizer, MAX_UPLOAD_BYTES
from .photo_policy import POLICY_VERSION
from .routes import RoutePlanner, RoutePreferences, RouteService, is_route_question
from .floor_demo import FloorDemo
from .operations_demo import OperationsDemo

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

class PhotoActionRequest(BaseModel):
    trace_id: str = Field(min_length=1, max_length=64)
    action: Literal["confirm", "view_similar", "retry", "reject", "search", "browse"]
    object_id: str | None = Field(default=None, max_length=100)

class ReviewRequest(BaseModel):
    status: Literal["pending", "confirmed", "fixed", "not_a_bug"]
    stage: Literal["retrieval", "vision", "generation", "verification", "service"] | None = None
    notes: str = Field(default="", max_length=1000)
    regression_trace_id: str | None = Field(default=None, max_length=64)

class EvalRunRequest(BaseModel):
    id: str = Field(alias="_id", min_length=1, max_length=64)
    created_at: float
    kind: Literal["text", "photo", "reliability"]
    status: Literal["completed", "failed"]
    dataset_version: str = Field(max_length=100)
    dataset_hash: str = Field(min_length=64, max_length=64)
    corpus_hash: str = Field(min_length=64, max_length=64)
    model: str = Field(max_length=100)
    prompt_version: str = Field(max_length=100)
    embedding_model: str = Field(default="", max_length=150)
    human_reviewed: bool = False
    online_ab: Literal[False] = False
    pricing: dict | None = None
    visual_index_hash: str | None = None
    photo_prompt_version: str | None = None
    summary: dict = Field(default_factory=dict)
    results: list[dict] = Field(default_factory=list, max_length=80)

class EvalGrade(BaseModel):
    case_id: str = Field(max_length=64)
    variant: str = Field(max_length=40)
    reviewer: str = Field(min_length=1, max_length=80)
    facts_correct: int = Field(ge=0)
    facts_total: int = Field(ge=0)
    citations_supported: int = Field(ge=0)
    citations_total: int = Field(ge=0)
    refusal_correct: bool | None = None
    style_passed: bool | None = None

def create_app(settings: MuseumSettings | None = None, client_factory=None):
    config = settings or MuseumSettings()
    locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def lifespan(app):
        mongo = None
        if config.museum_storage == "mongo":
            mongo = MuseumMongo(Settings(_env_file=None, storage_mode="mongo", mongodb_uri=config.mongodb_uri,
                                     mongodb_db=config.mongodb_db))
            await asyncio.wait_for(mongo.connect(), 10)
            store = RuntimeStore(MongoStore(mongo), mongo=mongo)
        else:
            store = RuntimeStore(MemoryStore())
        for claim in await store.find("museum_request_cache", {"state": "pending"}):
            if not await store.get("museum_traces", claim["trace_id"]):
                await store.upsert("museum_traces", {"_id": claim["trace_id"], "session_id": claim["session_id"],
                    "created_at": claim["created_at"], "query": claim.get("query", ""), "status": "interrupted", "failure_stage": "service"})
        # Single API process: unfinished work from the previous process must stay visible.
        for collection in ("museum_traces", "museum_photo_traces"):
            for row in await store.find(collection, {"status": "running"}):
                row.update(status="interrupted", failure_stage="service")
                await store.upsert(collection, row)
        index = MuseumIndex(config, store)
        await index.start()
        app.state.store, app.state.index = store, index
        app.state.engine = MuseumEngine(config, store, index, client_factory)
        app.state.routes = RouteService(RoutePlanner(config.museum_route_manifest), app.state.engine)
        app.state.floor_demo = FloorDemo(config.museum_floor_demo_manifest)
        app.state.operations_demo = OperationsDemo(config.museum_operations_demo_manifest, app.state.routes.planner)
        if config.museum_visual_manifest:
            from .visual_index import MuseumVisualIndex
            visual = MuseumVisualIndex(config.museum_visual_manifest, config.museum_visual_model, index.records,
                                       cache_dir=config.museum_visual_cache)
            await visual.start()
            if config.museum_photo_verification == "partial":
                from .local_correspondence import LocalCorrespondence
                visual.local_correspondence = LocalCorrespondence(visual, config.museum_visual_cache)
            app.state.engine.visual_index = visual
        app.state.slots = asyncio.Semaphore(config.museum_max_inflight)
        app.state.new_sessions = {}
        app.state.write_gate = WriteGate()
        app.state.backup = BackupManager(config, mongo, app.state.write_gate)
        backup_task = asyncio.create_task(app.state.backup.daily()) if config.museum_daily_backup else None
        try:
            await index.start_reranker()
            yield
        finally:
            await index.close()
            if backup_task:
                backup_task.cancel()
                try:
                    await backup_task
                except asyncio.CancelledError:
                    pass
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

    async def writing():
        async with app.state.write_gate.operation():
            yield

    async def failure_trace(current, query, status, action, started, error_type):
        trace_id = current.get("_trace_id") or uuid4().hex
        result = {"trace_id": trace_id, "status": status, "answer": "服务暂时未完成，请稍后重试。",
                  "claims": [], "sources": [], "mode": "brief", "usage": [],
                  "latency_ms": round((time.perf_counter() - started) * 1000)}
        row = {"_id": trace_id, "session_id": current["_id"], "created_at": time.time(),
               "status": status, "failure_stage": "service", "action": action, "error": error_type,
               "model": config.deepseek_model, "corpus_hash": app.state.index.corpus_hash,
               "prompt_version": PhotoRecognizer.PROMPT_VERSION if action == "photo" else MuseumEngine.PROMPT_VERSION,
               "result": result}
        if action != "photo":
            row["query"] = query
        else:
            row["photo_hash"] = current.get("_photo_hash")
            row["photo_hash_kind"] = "upload_bytes"
            row["parent_photo_trace_id"] = current.get("_parent_photo_trace_id")
            row["retake_count"] = current.get("_retake_count", 0)
        await app.state.store.upsert("museum_photo_traces" if action == "photo" else "museum_traces", row)
        return result

    @app.get("/api/museum/health")
    async def health():
        return {"status": "ok", "model_configured": bool(config.deepseek_api_key),
                "storage": config.museum_storage, "retrieval": config.museum_embedding,
                "corpus_count": len(app.state.index.records), "corpus_hash": app.state.index.corpus_hash,
                "text_rerank": {"enabled": config.museum_text_rerank,
                    "state": app.state.index.reranker.state if app.state.index.reranker else 'disabled',
                    "budget_ms": round(config.museum_rerank_timeout*1000)},
                "prompt_version": MuseumEngine.PROMPT_VERSION,
                "photo_prompt_version": PhotoRecognizer.PROMPT_VERSION,
                "photo_policy_version": POLICY_VERSION,
                "photo_reference_mode": config.museum_photo_reference_mode,
                "photo_verification": config.museum_photo_verification,
                "route_planning": app.state.routes.planner.unavailable() is None,
                "photo_retrieval": "image_and_text" if getattr(app.state.engine, "visual_index", None) else "caption_text",
                "visual_index_hash": getattr(getattr(app.state.engine, "visual_index", None), "index_hash", None)}

    @app.get("/api/museum/routes/options")
    async def route_options():
        return app.state.routes.planner.options()

    @app.get("/api/museum/routes/floor-demo")
    async def floor_demo():
        return app.state.floor_demo.describe()

    @app.get("/api/museum/demo/operations")
    async def operations_demo():
        # Public demo data only. It does not accept role flags or publish changes.
        return app.state.operations_demo.describe()

    @app.get("/api/museum/routes/floor-demo/tiles/{tile_id}")
    async def floor_demo_tile(tile_id: str):
        path = app.state.floor_demo.tile_path(tile_id)
        if path is None:
            raise HTTPException(404, "地图图片不可用")
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})

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

    @app.post("/api/museum/recognize", dependencies=[Depends(writing)])
    async def recognize(photo: UploadFile = File(...), parent_trace_id: str | None = Form(default=None, max_length=64), current=Depends(session)):
        if not config.deepseek_api_key:
            raise HTTPException(503, "照片识别尚未启用，请先选择示例作品")
        raw = await photo.read(MAX_UPLOAD_BYTES + 1)
        await photo.close()
        lock = locks.setdefault(current["_id"], asyncio.Lock())
        if lock.locked():
            raise HTTPException(409, "上一个问题仍在处理，请稍后")
        async with lock:
            retake_count = 0
            if parent_trace_id:
                parent = await app.state.store.get("museum_photo_traces", parent_trace_id)
                if not parent or parent["session_id"] != current["_id"]:
                    raise HTTPException(404, "未找到本会话的照片记录")
                retake_count = parent.get("retake_count", 0)
                # A service failure is not evidence that the visitor's photo is bad.
                if parent.get("status") != "service_unavailable":
                    if retake_count >= 1:
                        raise HTTPException(409, "已补拍一次，请输入作品名称或展签文字，或浏览馆藏")
                    retake_count += 1
            if app.state.slots.locked():
                raise HTTPException(429, "服务繁忙，请稍后重试")
            async with app.state.slots:
                started = time.perf_counter()
                current = await app.state.store.get("museum_sessions", current["_id"])
                if not current or current["expires_at"] <= time.time():
                    raise HTTPException(401, "会话已过期，请重新开始")
                # A new photo is an unresolved entity, not the previously discussed artwork.
                current.update(history=[], object_id=None)
                current.pop("photo_selection", None)
                await app.state.store.upsert("museum_sessions", current)
                current = {**current, "_trace_id": uuid4().hex, "_photo_hash": hashlib.sha256(raw).hexdigest(),
                           "_parent_photo_trace_id": parent_trace_id, "_retake_count": retake_count}
                await app.state.store.upsert("museum_photo_traces", {
                    "_id": current["_trace_id"], "session_id": current["_id"], "created_at": time.time(),
                    "status": "running", "photo_hash": current["_photo_hash"], "photo_hash_kind": "upload_bytes",
                    "parent_photo_trace_id": parent_trace_id, "retake_count": retake_count})
                try:
                    return await asyncio.wait_for(PhotoRecognizer(app.state.engine).recognize(raw, current), 65)
                except ValueError as exc:
                    await failure_trace(current, "", "invalid_image", "photo", started, type(exc).__name__)
                    raise HTTPException(422, str(exc)) from None
                except asyncio.TimeoutError:
                    await failure_trace(current, "", "timeout", "photo", started, "TimeoutError")
                    raise HTTPException(504, "照片识别服务超时，不代表照片有问题。可以稍后重试或输入作品名称") from None
                except Exception as exc:
                    await failure_trace(current, "", "service_unavailable", "photo", started, type(exc).__name__)
                    raise HTTPException(503, "照片识别未完成，失败记录已保留") from None

    @app.post("/api/museum/photo-actions", dependencies=[Depends(writing)])
    async def photo_action(body: PhotoActionRequest, current=Depends(session)):
        lock = locks.setdefault(current["_id"], asyncio.Lock())
        if lock.locked():
            raise HTTPException(409, "请等待当前请求结束")
        async with lock:
            trace = await app.state.store.get("museum_photo_traces", body.trace_id)
            if not trace or trace["session_id"] != current["_id"]:
                raise HTTPException(404, "未找到本会话的照片记录")
            current = await app.state.store.get("museum_sessions", current["_id"])
            if not current or current["expires_at"] <= time.time():
                raise HTTPException(401, "会话已过期，请重新开始")
            if body.action in {"retry", "reject", "search", "browse"}:
                if body.object_id:
                    raise HTTPException(422, "此操作无需选择作品")
                if (body.action == "retry" and trace.get("retake_count", 0) >= 1
                        and trace.get("status") != "service_unavailable"):
                    raise HTTPException(409, "已补拍一次，请改用文字查找或浏览馆藏")
                current.update(history=[], object_id=None)
                current.pop("photo_selection", None)
            else:
                allowed = trace.get("candidate_ids" if body.action == "confirm" else "similar_candidate_ids", [])
                if body.object_id not in allowed or body.object_id not in app.state.index.records:
                    raise HTTPException(422, "这件作品不属于该操作允许的候选")
                if body.action == "confirm":
                    # User assertion, not machine certainty or evaluation ground truth.
                    trace["user_confirmed_object_id"] = body.object_id
                current.update(history=[], object_id=body.object_id,
                    photo_selection={"action": body.action, "trace_id": body.trace_id, "object_id": body.object_id})
            event = {"action": body.action, "object_id": body.object_id, "created_at": time.time()}
            events = trace.get("interactions", [])
            if not events or any(events[-1].get(k) != event[k] for k in ("action", "object_id")):
                trace["interactions"] = (events + [event])[-20:]
            await app.state.store.upsert("museum_photo_traces", trace)
            await app.state.store.upsert("museum_sessions", current)
            return {"saved": True, "action": body.action, "object_id": body.object_id}

    @app.post("/api/museum/sessions", dependencies=[Depends(writing)])
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
        await store.upsert("museum_sessions", {"_id": sid, "history": [], "created_at": now, "expires_at": now + config.museum_session_ttl})
        return {"token": token, "expires_in": config.museum_session_ttl}

    @app.post("/api/museum/chat", dependencies=[Depends(writing)])
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
            cached = await app.state.store.get("museum_request_cache", cache_id)
            if cached:
                if cached["fingerprint"] != fingerprint:
                    raise HTTPException(409, "相同请求编号不能用于不同问题")
                if not cached.get("result"):
                    # Recover a completed trace if a crash happened before cache finalization.
                    trace = await app.state.store.get("museum_traces", cached["trace_id"])
                    if trace and trace.get("result"):
                        recovered_status = {"timeout": 504, "service_unavailable": 503}.get(trace.get("status"), 200)
                        cached.update(result=trace["result"], state="completed", http_status=recovered_status)
                        await app.state.store.upsert("museum_request_cache", cached)
                    else:
                        raise HTTPException(409, "该请求尚未完成或曾被中断，请先在评审记录确认状态，勿重复提交")
                if cached.get("http_status", 200) != 200:
                    raise HTTPException(cached["http_status"], "该请求未完成，失败记录已保留；重试请使用新的请求编号")
                return cached["result"]
            if app.state.slots.locked():
                raise HTTPException(429, "服务正在处理其他问题，请稍后重试")
            # Re-read inside the lock; prevents overwriting history from a stale snapshot.
            current = await app.state.store.get("museum_sessions", current["_id"])
            if not current or current["expires_at"] <= time.time():
                raise HTTPException(401, "会话已过期，请重新开始")
            context = current.get("photo_selection")
            if context and body.object_id is not None and body.object_id != context["object_id"]:
                current.pop("photo_selection", None)
            claimed = {"_id": cache_id, "session_id": current["_id"], "request_id": str(body.request_id),
                       "query": body.query,
                       "fingerprint": fingerprint, "trace_id": uuid4().hex, "state": "pending",
                       "created_at": time.time(), "expires_at": current["expires_at"]}
            if not await app.state.store.claim_request(claimed):
                raise HTTPException(409, "该请求已被处理，请稍后查询")
            current["_trace_id"] = claimed["trace_id"]
            await app.state.store.upsert("museum_traces", {
                "_id": claimed["trace_id"], "session_id": current["_id"], "created_at": claimed["created_at"],
                "status": "running", "query": body.query, "action": body.action,
                "corpus_hash": app.state.index.corpus_hash, "model": config.deepseek_model,
                "prompt_version": MuseumEngine.PROMPT_VERSION})
            started = time.perf_counter()
            http_status = 200
            async with app.state.slots:
                try:
                    if route_request:
                        work = app.state.routes.handle(body.query.strip(), current, body.object_id, body.route)
                    else:
                        handler = app.state.engine.narrate if body.action == "narration" else app.state.engine.answer
                        work = handler(body.query.strip(), current, body.mode, body.object_id)
                    result = await asyncio.wait_for(work, timeout=config.museum_timeout)
                except asyncio.TimeoutError:
                    http_status = 504
                    result = await failure_trace(current, body.query, "timeout", body.action, started, "TimeoutError")
                except Exception as exc:
                    http_status = 503
                    result = await failure_trace(current, body.query, "service_unavailable", body.action, started, type(exc).__name__)
            current.pop("_trace_id", None)
            if current.get("photo_selection"):
                result["photo_selection"] = current["photo_selection"]
                if current["photo_selection"]["action"] == "view_similar":
                    result["context_notice"] = "以下介绍的是你选择查看的相似馆藏，不代表已确认上传照片中的作品。"
                trace = await app.state.store.get("museum_traces", claimed["trace_id"])
                trace.update(result=result, photo_selection=current["photo_selection"])
                await app.state.store.upsert("museum_traces", trace)
            await app.state.store.upsert("museum_sessions", current)
            await app.state.store.upsert("museum_request_cache", {**claimed, "state": "completed", "result": result, "http_status": http_status})
            if http_status != 200:
                raise HTTPException(http_status, "请求未完成，失败记录已保留")
            return result

    @app.post("/api/museum/feedback", dependencies=[Depends(writing)])
    async def feedback(body: FeedbackRequest, current=Depends(session)):
        trace = await app.state.store.get("museum_traces", body.trace_id) or await app.state.store.get("museum_photo_traces", body.trace_id)
        if not trace or trace["session_id"] != current["_id"]:
            raise HTTPException(404, "未找到本会话的回答")
        await app.state.store.upsert("museum_feedback", {"_id": body.trace_id, "session_id": current["_id"],
            **body.model_dump(), "updated_at": time.time(), "review_status": "pending"})
        return {"saved": True}

    @app.post("/api/museum/session/close", dependencies=[Depends(writing)])
    async def close_session(current=Depends(session)):
        lock = locks.setdefault(current["_id"], asyncio.Lock())
        if lock.locked():
            raise HTTPException(409, "请等待当前回答结束")
        async with lock:
            await app.state.store.delete("museum_sessions", current["_id"])
        locks.pop(current["_id"], None)
        return {"closed": True, "records_retained": True}

    @app.get("/api/museum/traces/{trace_id}")
    async def own_trace(trace_id: str, current=Depends(session)):
        row = await app.state.store.get("museum_traces", trace_id) or await app.state.store.get("museum_photo_traces", trace_id)
        if not row or row["session_id"] != current["_id"]:
            raise HTTPException(404, "未找到本会话的记录")
        return row.get("result", {"trace_id": trace_id, "status": row.get("status")})

    @app.delete("/api/museum/session", dependencies=[Depends(writing)])
    async def delete_session(current=Depends(session)):
        lock = locks.setdefault(current["_id"], asyncio.Lock())
        if lock.locked():
            raise HTTPException(409, "请等待当前回答结束后清除")
        async with lock:
            for table in ["museum_traces", "museum_feedback", "museum_photo_traces"]:
                for row in await app.state.store.find(table, {"session_id": current["_id"]}):
                    await app.state.store.delete(table, row["_id"])
            for row in await app.state.store.find("museum_request_cache", {"session_id": current["_id"]}):
                await app.state.store.delete("museum_request_cache", row["_id"])
            await app.state.store.delete("museum_sessions", current["_id"])
        locks.pop(current["_id"], None)
        return {"deleted": True}

    @app.get("/api/museum/admin/traces", dependencies=[Depends(admin)])
    async def traces(stage: str | None = None, status: str | None = None, session_id: str | None = None,
                     before: float | None = None, before_id: str | None = None, limit: int = 100):
        if stage is not None and stage not in FAILURE_STAGES or not 1 <= limit <= 500:
            raise HTTPException(422, "筛选条件无效")
        return await review_rows(app.state.store, stage=stage, status=status, session_id=session_id, before=before, before_id=before_id, limit=limit)

    @app.post("/api/museum/admin/reviews/{kind}/{trace_id}", dependencies=[Depends(admin), Depends(writing)])
    async def review(kind: Literal["text", "photo"], trace_id: str, body: ReviewRequest):
        collection = "museum_traces" if kind == "text" else "museum_photo_traces"
        row = await app.state.store.get(collection, trace_id)
        if not row:
            raise HTTPException(404, "记录不存在")
        if body.regression_trace_id and not (await app.state.store.get("museum_traces", body.regression_trace_id) or await app.state.store.get("museum_photo_traces", body.regression_trace_id)):
            raise HTTPException(422, "回归记录不存在")
        row["review"] = {**body.model_dump(), "updated_at": time.time()}
        if body.stage:
            row["failure_stage"] = body.stage
        await app.state.store.upsert(collection, row)
        return {"saved": True}

    @app.get("/api/museum/admin/export", dependencies=[Depends(admin)])
    async def export(stage: str | None = None, status: str | None = None, session_id: str | None = None, before: float | None = None, before_id: str | None = None):
        if stage is not None and stage not in FAILURE_STAGES:
            raise HTTPException(422, "筛选条件无效")
        data = await review_rows(app.state.store, stage=stage, status=status, session_id=session_id, before=before, before_id=before_id, limit=500)
        return export_metrics(data)

    @app.get("/api/museum/admin/operations", dependencies=[Depends(admin)])
    async def operations():
        return {"storage": config.museum_storage, "daily_backup": config.museum_daily_backup,
                "backup": app.state.backup.status, "eval_runs": await app.state.store.find("eval_runs", limit=20)}

    @app.post("/api/museum/admin/eval-runs", dependencies=[Depends(admin), Depends(writing)])
    async def save_eval(body: EvalRunRequest):
        payload = body.model_dump(by_alias=True)
        encoded = json.dumps(payload)
        if len(encoded) > 1_000_000 or "data:image" in encoded or any("result" in r for r in body.results):
            raise HTTPException(422, "这里只保存精简评测指标，不接收原始照片或完整回答")
        if not await app.state.store.insert_unique("eval_runs", payload):
            raise HTTPException(409, "评测批次已存在，不能覆盖")
        return {"saved": True, "run_id": body.id}

    @app.post("/api/museum/admin/eval-runs/{run_id}/grades", dependencies=[Depends(admin), Depends(writing)])
    async def grade_eval(run_id: str, body: EvalGrade):
        if body.facts_correct > body.facts_total or body.citations_supported > body.citations_total:
            raise HTTPException(422, "正确数不能超过总数")
        row = await app.state.store.get("eval_runs", run_id)
        if not row:
            raise HTTPException(404, "评测批次不存在")
        target = next((r for r in row["results"] if r["case_id"] == body.case_id and r["variant"] == body.variant), None)
        if target is None:
            raise HTTPException(404, "评测题目不存在")
        target["human_grade"] = {**body.model_dump(), "reviewed_at": time.time()}
        row["human_grades_completed"] = sum(r.get("human_grade") is not None for r in row["results"])
        await app.state.store.upsert("eval_runs", row)
        return {"saved": True, "graded": row["human_grades_completed"], "total": len(row["results"])}

    @app.post("/api/museum/admin/backup", dependencies=[Depends(admin)])
    async def backup():
        try:
            return await app.state.backup.run()
        except Exception:
            raise HTTPException(503, "备份未完成，请检查本地工具与备份状态") from None

    return app

app = create_app()
