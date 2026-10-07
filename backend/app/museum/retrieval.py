from __future__ import annotations
import asyncio
import hashlib
import json
import textwrap
import re
import unicodedata
from typing import Any
from app.config import Settings
from app.llm.embeddings import EmbeddingClient
from app.retrieval.bm25 import BM25Index
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.reranker import Reranker
from app.retrieval.vector_store import MemoryVectorStore
from .config import MuseumSettings, ROOT
from .semantic_chunks import dense_views, administrative_view

def canonical_accession(value: str) -> str | None:
    """Normalize a complete bare identifier, never extract one from prose.

    Separator removal may collide; the index deliberately retains all records.
    Only the documented punctuation set is removed, not arbitrary characters.
    """
    if not isinstance(value, str):
        return None
    value = unicodedata.normalize('NFKC', value).strip().upper()
    value = value.translate(str.maketrans({'–':'-', '—':'-', '−':'-'}))
    if not value or len(value) > 100 or not re.fullmatch(r'[A-Z0-9\s.\-:/,()&]+', value):
        return None
    key = re.sub(r'[\s.\-:/,()&]', '', value)
    return key if any(c.isdigit() for c in key) else None

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
        self.accessions: dict[str, list[str]] = {}
        self.hybrid = HybridRetriever(self.bm25, self.vectors, FusionOrder(), bm25_top=25, vector_top=25, top_k=25)

    async def start(self):
        raw = self.settings.museum_corpus.read_bytes()
        self.corpus_hash = hashlib.sha256(raw).hexdigest()
        records = json.loads(raw)
        if self.settings.museum_private_corpus:
            private_raw = self.settings.museum_private_corpus.read_bytes()
            records = json.loads(private_raw) + records
            self.corpus_hash = hashlib.sha256(raw + b'\n' + private_raw).hexdigest()
        if not records:
            raise ValueError("Corpus is empty; run scripts.import_museum first")
        self.records = {r["_id"]: r for r in records if r.get("status") == "active"}
        if len(self.records) != len(records):
            raise ValueError("Corpus contains duplicates or inactive records")
        self.accessions = {}
        for r in records:
            key = canonical_accession(r.get('fields', {}).get('accession_number', ''))
            if key:
                self.accessions.setdefault(key, []).append(r['_id'])
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
            dense_chunks = chunks
            if self.settings.museum_dense_view == 'filtered':
                dense_chunks = dense_views(records)['filtered']
            elif self.settings.museum_dense_view == 'administrative':
                dense_chunks = administrative_view(records)['chunks']
            vectors = await asyncio.to_thread(self.embedding._local_embed, [r["content"] for r in dense_chunks]) if dense_chunks else []
            for r, vector in zip(dense_chunks, vectors, strict=True):
                await self.vectors.add(r["_id"], vector, r)

    def exact_accession_ids(self, query: str) -> list[str]:
        return list(self.accessions.get(canonical_accession(query), []))

    async def _current_sources(self, ids):
        results = []
        for source_id in dict.fromkeys(ids):
            source = await self.store.get('museum_sources', source_id)
            expected = self.records.get(source_id)
            if source and expected and source.get('status') == 'active' and source['source_hash'] == expected['source_hash']:
                results.append(source)
        return results

    async def search(self, query: str, object_id: str | None = None, variant: str = "hybrid"):
        if object_id and object_id not in self.records:
            return []
        exact_ids = self.exact_accession_ids(query) if not object_id else []
        if exact_ids:
            # Full field equality outranks neither context nor source validity.
            # No fuzzy fallback if the exact record has since been withdrawn.
            return await self._current_sources(exact_ids)
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
