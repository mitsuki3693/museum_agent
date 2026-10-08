from pathlib import Path
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

ROOT = Path(__file__).resolve().parents[3]

class MuseumSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    museum_storage: Literal["memory", "mongo"] = "memory"
    mongodb_uri: str = "mongodb://127.0.0.1:27017"
    mongodb_db: str = "museum_agent"
    museum_embedding: Literal["lexical", "local"] = "lexical"
    museum_embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    # Opt-in after corpus-specific acceptance. Original is the rollback/default.
    museum_dense_view: Literal['original', 'filtered', 'administrative'] = 'original'
    museum_text_rerank: bool = False
    museum_rerank_sort_by_length: bool = False
    # Optional retrieval-only Chinese titles; default excludes draft annotations.
    museum_search_fields: Path | None = None
    museum_search_allow_drafts: bool = False
    museum_rerank_model: Path = ROOT / 'models/bge-reranker-v2-m3'
    museum_rerank_timeout: float = Field(default=8,gt=0,le=20)
    museum_rerank_startup_timeout: float = Field(default=60,gt=0,le=120)
    museum_admin_token: str = ""
    museum_session_ttl: int = 1800
    museum_max_inflight: int = 2
    museum_mongodump: Path | None = None
    museum_backup_dir: Path = ROOT / ".runtime/backups"
    museum_daily_backup: bool = False
    museum_timeout: float = 100
    museum_corpus: Path = ROOT / "data/corpus.json"
    museum_private_corpus: Path | None = None
    # Opt-in: unset manifest is the explicit rollback to caption/text retrieval.
    museum_visual_manifest: Path | None = None
    museum_visual_model: Path = ROOT / "models/dinov2-small"
    museum_visual_cache: Path = ROOT / ".runtime/visual-features"
    # Default unchanged until paired verification demonstrates a benefit.
    museum_photo_reference_mode: Literal["single", "multiview"] = "single"
    museum_photo_verification: Literal["legacy", "visibility", "candidate", "partial"] = "legacy"
    # A separately sourced, optional route snapshot; never inferred from artwork descriptions.
    museum_route_manifest: Path | None = None
    museum_floor_demo_manifest: Path | None = None
    museum_operations_demo_manifest: Path | None = None
