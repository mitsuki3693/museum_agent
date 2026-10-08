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
from .work_fusion import fuse_works

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
        self.reranker = None
        self.rerank_vectors = None
        self.search_fields = {}
        self.search_fields_meta = None
        self.chinese_recall = None
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
        if self.settings.museum_search_fields:
            from .search_fields import load_search_fields
            path=self.settings.museum_search_fields
            if not path.is_absolute():path=ROOT/path
            self.search_fields,self.search_fields_meta=load_search_fields(
                path,self.records,allow_drafts=self.settings.museum_search_allow_drafts)
            if self.settings.museum_chinese_recall:
                from .chinese_recall import ChineseRecall
                self.chinese_recall=ChineseRecall(path,self.records,self.search_fields,self.search_fields_meta)
        elif self.settings.museum_chinese_recall:
            raise ValueError('Chinese recall requires a validated search-field manifest')
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
            if self.settings.museum_text_rerank:
                # Separate experimental lane: fallback and photo paths retain
                # their configured dense view, even when it is original.
                self.rerank_vectors=MemoryVectorStore()
                admin_chunks=administrative_view(records)['chunks']
                # Batch-dependent encoder rounding can reorder tied chunks.
                # Reproduce the frozen administrative sequence, not a subset
                # of vectors encoded in another batch (original/filtered).
                admin_vectors=vectors if self.settings.museum_dense_view=='administrative' else (
                    await asyncio.to_thread(self.embedding._local_embed,[c['content'] for c in admin_chunks]) if admin_chunks else [])
                for chunk,vector in zip(admin_chunks,admin_vectors,strict=True):
                    await self.rerank_vectors.add(chunk['_id'],vector,chunk)

    async def start_reranker(self):
        if self.settings.museum_text_rerank and self.rerank_vectors is not None:
            from .rerank_service import RerankService
            self.reranker=RerankService(self.settings.museum_rerank_model,
                timeout=self.settings.museum_rerank_timeout,startup_timeout=self.settings.museum_rerank_startup_timeout,
                sort_by_length=self.settings.museum_rerank_sort_by_length,
                evidence_controls=self.settings.museum_chinese_recall)
            await self.reranker.start()

    async def close(self):
        if self.reranker:await self.reranker.stop()

    async def search_for_answer(self, query, object_id=None, variant='hybrid'):
        # A complete annotated title must not be lost to generic dense votes.
        # Preserve homonyms and provenance checks; this is not photo identity.
        if self.search_fields and not object_id and variant=='hybrid' and not self.exact_accession_ids(query):
            from .recall_fields import named_title_ids,order_named_ids
            named=named_title_ids(query,self.search_fields)
            if named:
                if len(named)>1:
                    prior=await self.search(query,object_id,variant)
                    named=order_named_ids(named,[r['_id'] for r in prior])
                rows=await self._current_sources(named)
                return rows,dict(status='named_title',candidate_ids=[s['_id'] for s in rows],
                                 search_fields=self.search_fields_meta)
        baseline=await self.search(query,object_id,variant)
        recall_enabled=bool(self.chinese_recall and not object_id and variant=='hybrid' and not self.exact_accession_ids(query))
        fallback_material=None
        glossary=None
        if recall_enabled and self.chinese_recall.applies(query):
            from .rerank_evidence_controls import prioritize_material
            vector=(await asyncio.to_thread(self.embedding._local_embed,[query]))[0] if self.embedding else None
            dense=await self.vectors.search(vector,top_k=25) if vector is not None else []
            expanded=None
            if self.settings.museum_fallback_glossary:
                from .fallback_glossary import expand_query
                expanded,glossary=expand_query(query)
            fallback=self.chinese_recall.pool(query,self.bm25,dense,lexical_override=expanded)
            ids=[c['source_id'] for c in fallback]
            current=await self._current_sources(ids)
            ids,fallback_material=prioritize_material(query,[r['_id'] for r in current],{r['_id']:r for r in current})
            baseline=(await self._current_sources(ids))[:5]
        trace=dict(status='disabled',fallback_ids=[s['_id'] for s in baseline])
        if recall_enabled:
            from .chinese_recall import VERSION
            trace.update(recall_version=VERSION,search_fields=self.search_fields_meta,
                         fallback_material_status=(fallback_material or {}).get('status'))
            if glossary:trace['fallback_glossary']=glossary
        if not self.settings.museum_text_rerank:return baseline,trace
        if object_id or self.exact_accession_ids(query) or variant!='hybrid':
            trace['status']='bypassed';return baseline,trace
        if not self.reranker or self.rerank_vectors is None:
            trace['status']='unavailable';return baseline,trace
        # Avoid re-encoding or extra retrieval while the worker is disabled/busy.
        if self.reranker.state!='ready' or self.reranker.busy:
            trace['status']='busy' if self.reranker.busy else self.reranker.state
            return baseline,trace
        try:
            vector=(await asyncio.to_thread(self.embedding._local_embed,[query]))[0]
            candidates=fuse_works(self.bm25.search(query,top_k=25),await self.rerank_vectors.search(vector,top_k=25))
            if recall_enabled:
                candidates=self.chinese_recall.pool(query,self.bm25,await self.rerank_vectors.search(vector,top_k=25))
            sources=await self._current_sources([c['source_id'] for c in candidates])
            active={s['_id'] for s in sources};candidates=[c for c in candidates if c['source_id'] in active]
            if not candidates:
                trace['status']='no_candidates';return baseline,trace
            if recall_enabled:
                candidates,trace['bridge']=self.chinese_recall.bridge(query,candidates,sources)
            ids,details=await self.reranker.rank(query,candidates,sources)
            trace.update(details)
            if ids is not None:
                return (await self._current_sources(ids))[:5],trace
        except Exception as exc:
            trace.update(status='error',error_type=type(exc).__name__)
        # Revalidate sources after a potentially long wait; never resurrect an
        # archived or replaced source via the fallback snapshot.
        return await self._current_sources([s['_id'] for s in baseline]),trace

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
