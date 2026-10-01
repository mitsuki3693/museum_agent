"""Local standalone backups: quiesced application writes, verified collection digests."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from bson import json_util
from .runtime import RUNTIME_COLLECTIONS


async def database_manifest(db):
    result = {}
    for name in RUNTIME_COLLECTIONS:
        rows = [r async for r in db[name].find({}).sort("_id", 1)]
        raw = json_util.dumps(rows, json_options=json_util.CANONICAL_JSON_OPTIONS, sort_keys=True).encode()
        result[name] = {"count": len(rows), "sha256": hashlib.sha256(raw).hexdigest()}
    return result


class BackupManager:
    def __init__(self, settings, mongo, gate):
        self.settings, self.mongo, self.gate = settings, mongo, gate
        self.directory = settings.museum_backup_dir
        self.state_file = self.directory / "status.json"
        self.status = {"status": "disabled" if not mongo or not settings.museum_mongodump else "pending"}
        if mongo and settings.museum_mongodump and self.state_file.exists():
            self.status = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.lock = asyncio.Lock()

    async def run(self):
        if not self.mongo or not self.settings.museum_mongodump:
            raise ValueError("MongoDB backup tool is not configured")
        async with self.lock, self.gate.maintenance():
            self.directory.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            archive = self.directory / (stamp + ".archive.gz")
            started = time.time()
            try:
                before = await database_manifest(self.mongo.db)
                # URI is inherited via config file so credentials never enter process arguments.
                config = self.directory / (stamp + ".config.yml")
                config.write_text("uri: " + json.dumps(self.settings.mongodb_uri) + "\n", encoding="utf-8")
                try:
                    proc = await asyncio.create_subprocess_exec(str(self.settings.museum_mongodump),
                        "--config=" + str(config), "--db=" + self.settings.mongodb_db,
                        "--archive=" + str(archive), "--gzip", stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL)
                    try:
                        code = await asyncio.wait_for(proc.wait(), 180)
                    except BaseException:
                        if proc.returncode is None:
                            proc.kill()
                        await proc.wait()
                        raise
                    if code:
                        raise RuntimeError("mongodump failed")
                finally:
                    config.unlink(missing_ok=True)
                after = await database_manifest(self.mongo.db)
                if before != after:
                    raise RuntimeError("Database changed during backup (including TTL cleanup); snapshot not certified")
                self.status = {"status": "completed", "started_at": started, "completed_at": time.time(),
                               "archive": archive.name, "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                               "database": self.settings.mongodb_db, "collections": after,
                               "scope": "single application writer; no external writes during snapshot"}
                (self.directory / (stamp + ".manifest.json")).write_text(json.dumps(self.status, indent=2), encoding="utf-8")
            except BaseException as exc:
                self.status = {"status": "failed", "started_at": started, "error_type": type(exc).__name__}
                raise
            finally:
                tmp = self.state_file.with_suffix(".tmp")
                tmp.write_text(json.dumps(self.status, indent=2), encoding="utf-8")
                tmp.replace(self.state_file)
            return self.status

    async def daily(self):
        # Service-owned daily backup; when offline, catch up on next startup. No deletion/retention purge.
        while True:
            if self.mongo and self.settings.museum_mongodump:
                last = self.status.get("completed_at", self.status.get("started_at", 0))
                if time.time() - last >= 86400:
                    try:
                        await self.run()
                    except Exception:
                        pass  # persisted status is visible to the maintainer; retry at next daily interval
            await asyncio.sleep(60)
