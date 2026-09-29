from __future__ import annotations
import asyncio
import hashlib
import json
import textwrap
from typing import Any
from app.config import Settings
from app.llm.embeddings import EmbeddingClient
from app.retrieval.bm25 import BM25Index
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.reranker import Reranker
from app.retrieval.vector_store import MemoryVectorStore
from .config import MuseumSettings, ROOT

class FusionOrder(Reranker):
    """Preserve RRF scores. No neural reranker is claimed in the MVP."""
    async def rerank(self, query, candidates, top_k=5):
        return sorted(candidates, key=lambda c: c.get("_rrf", 0), reverse=True)[:top_k]

class MuseumIndex:
    def __init__(self, settings: MuseumSettings, store):
        self.settings, self.store = settings, store
        self.bm25 = BM25Index()
        self.vectors = MemoryVectorStore()
        self.embedding = None
        self.records: dict[str, dict[str, Any]] = {}
        self.corpus_hash = ""
        self.hybrid = HybridRetriever(self.bm25, self.vectors, FusionOrder(), bm25_top=25, vector_top=25, top_k=25)

    async def start(self):
        raw = self.settings.museum_corpus.read_bytes()
        self.corpus_hash = hashlib.sha256(raw).hexdigest()
        records = json.loads(raw)
        if not records:
            raise ValueError("Corpus is empty; run scripts.import_museum first")
        self.records = {r["_id"]: r for r in records if r.get("status") == "active"}
        if len(self.records) != len(records):
            raise ValueError("Corpus contains duplicates or inactive records")
        for r in records:
            await self.store.upsert("museum_sources", r)
        chunks = []
        for r in records:
            pieces = [p for line in r["content"].splitlines() for p in textwrap.wrap(line, width=220)]
            for i, piece in enumerate(pieces):
                chunks.append({"_id": f'{r["_id"]}::{i}', "source_id": r["_id"],
                               "content": r["title"] + "\n" + piece})
        self.bm25.index(chunks)
        if self.settings.museum_embedding == "local":
            self.embedding = EmbeddingClient(Settings(
                _env_file=None, embedding_provider="local",
                embedding_model=self.settings.museum_embedding_model, embedding_dim=384))
            # Fail closed on absent model/dependencies; never mix hash with semantic vectors.
            from sentence_transformers import SentenceTransformer
            self.embedding._local_model = await asyncio.to_thread(
                SentenceTransformer, self.settings.museum_embedding_model,
                cache_folder=str(ROOT / "models"), local_files_only=True)
            vectors = await asyncio.to_thread(self.embedding._local_embed, [r["content"] for r in chunks])
            for r, vector in zip(chunks, vectors, strict=True):
                await self.vectors.add(r["_id"], vector, r)

    async def search(self, query: str, object_id: str | None = None, variant: str = "hybrid"):
        if object_id and object_id not in self.records:
            return []
        if self.embedding is not None and variant == "hybrid":
            vector = (await asyncio.to_thread(self.embedding._local_embed, [query]))[0]
            hits = await self.hybrid.retrieve(query, vector)
        else:
            hits = self.bm25.search(query, top_k=25)
        # Explicit selected object is a product context, not an LLM guess.
        if object_id:
            hits = [{"id": object_id, "source_id": object_id}] + hits
        results = []
        seen = set()
        for hit in hits:
            source_id = hit["source_id"]
            if source_id in seen:
                continue
            seen.add(source_id)
            source = await self.store.get("museum_sources", source_id)
            expected = self.records.get(source_id)
            if source and expected and source.get("status") == "active" and source["source_hash"] == expected["source_hash"]:
                results.append(source)
                if len(results) == 5:
                    break
        return results
