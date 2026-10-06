"""Museum runtime persistence. Source records stay in an isolated in-memory JSON view."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import hmac
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError
from app.storage.mongodb import MongoDB
from app.storage.store import MemoryStore, MongoStore

RUNTIME_COLLECTIONS = (
    "museum_sessions", "museum_traces", "museum_photo_traces", "museum_feedback",
    "museum_request_cache", "eval_runs",
)
FAILURE_STAGES = ("retrieval", "vision", "generation", "verification", "service")


class MuseumMongo(MongoDB):
    async def connect(self):
        from motor.motor_asyncio import AsyncIOMotorClient
        self.client = AsyncIOMotorClient(self.settings.mongodb_uri, serverSelectionTimeoutMS=5000,
                                         tz_aware=True, w=1, journal=True)
        self.db = self.client[self.settings.mongodb_db]
        try:
            await self.client.admin.command("ping")
            await self._ensure_indexes()
        except BaseException:
            self.client.close()
            raise

    async def _ensure_indexes(self):
        # Fail startup on index errors; the TTL monitor requires a BSON Date, not a float.
        for name in ("museum_sessions", "museum_request_cache"):
            await self.db[name].create_index("purge_at", expireAfterSeconds=0)
        for name in ("museum_traces", "museum_photo_traces"):
            await self.db[name].create_index([("created_at", -1), ("_id", -1)])
            await self.db[name].create_index([("session_id", 1), ("created_at", -1)])
            await self.db[name].create_index([("status", 1), ("created_at", -1)])
            await self.db[name].create_index([("failure_stage", 1), ("created_at", -1)])
        await self.db.museum_feedback.create_index([("session_id", 1), ("updated_at", -1)])
        await self.db.museum_request_cache.create_index([("session_id", 1), ("request_id", 1)], unique=True)
        await self.db.eval_runs.create_index([("created_at", -1)])
        await self.db.eval_runs.create_index([("dataset_hash", 1), ("corpus_hash", 1)])


def infer_failure(doc, photo=False):
    status = doc.get("status", doc.get("result", {}).get("status"))
    if status in ("service_unavailable", "timeout", "interrupted", "invalid_image"):
        return "service"
    if status == "verification_failed":
        return "verification"
    if photo and status == "not_matched":
        return "vision"
    if status == "insufficient_evidence":
        return "retrieval" if not doc.get("result", {}).get("retrieved_ids") else "generation"
    return None


class RuntimeStore:
    def __init__(self, backend, *, mongo=None):
        self.backend, self.mongo = backend, mongo
        self.sources = MemoryStore()

    def target(self, collection):
        if collection == "museum_sources":
            return self.sources
        if collection not in RUNTIME_COLLECTIONS:
            raise ValueError("Unknown museum runtime collection")
        return self.backend

    async def upsert(self, collection, doc):
        row = copy.deepcopy(doc)
        if collection in ("museum_sessions", "museum_request_cache"):
            row["purge_at"] = datetime.fromtimestamp(row["expires_at"], timezone.utc)
        if collection in ("museum_traces", "museum_photo_traces"):
            row.setdefault("status", row.get("result", {}).get("status", "unknown"))
            row.setdefault("failure_stage", infer_failure(row, collection == "museum_photo_traces"))
            row.setdefault("review", {"status": "pending", "stage": None})
        await self.target(collection).upsert(collection, row)

    async def get(self, collection, key):
        return await self.target(collection).get(collection, key)

    async def find(self, collection, query=None, limit=0):
        if self.mongo and collection != "museum_sources":
            cursor = self.mongo.collection(collection).find(query or {}).sort([("created_at", -1), ("_id", -1)])
            if limit:
                cursor = cursor.limit(limit)
            return [row async for row in cursor]
        return await self.target(collection).find(collection, query, limit)

    async def delete(self, collection, key):
        await self.target(collection).delete(collection, key)

    async def count(self, collection, query=None):
        return await self.target(collection).count(collection, query)

    async def claim_request(self, row):
        """Atomic insert; callers never re-execute an unresolved claim after a crash."""
        row = {**row, "purge_at": datetime.fromtimestamp(row["expires_at"], timezone.utc)}
        return await self.insert_unique("museum_request_cache", row)

    async def insert_unique(self, collection, row):
        self.target(collection)
        if self.mongo:
            try:
                await self.mongo.collection(collection).insert_one(copy.deepcopy(row))
                return True
            except DuplicateKeyError:
                return False
        if await self.get(collection, row["_id"]):
            return False
        await self.upsert(collection, row)
        return True


class WriteGate:
    """Quiesce one application's mutating requests while its backup is running."""
    def __init__(self):
        self.condition = asyncio.Condition()
        self.active, self.paused = 0, False

    @asynccontextmanager
    async def operation(self):
        async with self.condition:
            await self.condition.wait_for(lambda: not self.paused)
            self.active += 1
        try:
            yield
        finally:
            async with self.condition:
                self.active -= 1
                self.condition.notify_all()

    @asynccontextmanager
    async def maintenance(self):
        async with self.condition:
            await self.condition.wait_for(lambda: not self.paused)
            self.paused = True
        try:
            async with self.condition:
                await self.condition.wait_for(lambda: self.active == 0)
            yield
        finally:
            async with self.condition:
                self.paused = False
                self.condition.notify_all()


async def review_rows(store, *, stage=None, status=None, session_id=None, before=None, before_id=None, limit=100):
    query = {}
    if stage:
        query["failure_stage"] = stage
    if status:
        query["status"] = status
    if session_id:
        query["session_id"] = session_id
    if before is not None and store.mongo:
        query["$or"] = [{"created_at": {"$lt": before}}, {"created_at": before, "_id": {"$lt": before_id}}] if before_id else [{"created_at": {"$lt": before}}]
    groups = {}
    for collection in ("museum_traces", "museum_photo_traces"):
        rows = await store.find(collection, query, limit if store.mongo else 0)
        if before is not None and not store.mongo:
            rows = [r for r in rows if r["created_at"] < before or (r["created_at"] == before and before_id and r["_id"] < before_id)]
        groups[collection] = rows
    # One shared chronological page prevents one collection skipping the other's rows.
    merged = sorted([(name, row) for name, rows in groups.items() for row in rows],
                    key=lambda pair: (pair[1]["created_at"], pair[1]["_id"]), reverse=True)
    page = merged[:limit]
    groups = {name: [row for group, row in page if group == name] for name in groups}
    selected = {r["_id"] for rows in groups.values() for r in rows}
    feedback = [r for r in await store.find("museum_feedback", {"_id": {"$in": list(selected)}} if store.mongo else None) if r["trace_id"] in selected]
    cursor = {"before": page[-1][1]["created_at"], "before_id": page[-1][1]["_id"]} if len(page) == limit else None
    return {"traces": groups["museum_traces"], "photo_traces": groups["museum_photo_traces"], "feedback": feedback, "next_cursor": cursor}


def export_metrics(data):
    """Allowlist-only export: no free text, session hashes, photos or source passages."""
    salt = secrets.token_bytes(32)
    def anon(value):
        return hmac.new(salt, str(value).encode(), hashlib.sha256).hexdigest()[:24]
    rows = []
    for kind in ("traces", "photo_traces"):
        for row in data[kind]:
            result = row.get("result", row)
            usage = result.get("usage", [])
            tokens = {k: sum(int(x.get(k, 0)) for x in usage) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
            rows.append({"id": anon(row["_id"]), "session": anon(row["session_id"]), "kind": kind,
                         "created_at": row["created_at"], "status": row.get("status"),
                         "failure_stage": row.get("failure_stage"), "review_status": row.get("review", {}).get("status"),
                         "corpus_hash": row.get("corpus_hash"), "prompt_version": row.get("prompt_version"),
                         "model": row.get("model"), "visual_index_hash": row.get("visual_index_hash"),
                         "policy_version": row.get("policy_version"),
                         "comparison_reference_ids": row.get("comparison_reference_ids", []),
                         "comparison_image_ids": row.get("comparison_image_ids", []),
                         "reference_mode": row.get("reference_mode", "single"),
                         "reference_rescued_ids": row.get("reference_rescued_ids", []),
                         "retrieved_ids": result.get("retrieved_ids", row.get("visual_retrieved_ids", [])),
                         "candidate_ids": row.get("candidate_ids", []), "latency_ms": result.get("latency_ms"),
                         "match_state": row.get("match_state"), "similar_candidate_ids": row.get("similar_candidate_ids", []),
                         "user_confirmed_object_id": row.get("user_confirmed_object_id"),
                         "parent_photo": anon(row["parent_photo_trace_id"]) if row.get("parent_photo_trace_id") else None,
                         "interaction_actions": [e["action"] for e in row.get("interactions", [])],
                         "tokens": tokens})
    return {"schema_version": 1, "kind": "anonymized_metrics", "generated_at": time.time(), "rows": rows,
            "feedback": [{"trace": anon(f["trace_id"]), "kind": f["kind"]} for f in data["feedback"]],
            "excluded": ["questions", "answers", "quotes", "notes", "photos", "session_credentials"]}
