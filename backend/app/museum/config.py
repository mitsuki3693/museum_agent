from pathlib import Path
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    museum_admin_token: str = ""
    museum_session_ttl: int = 1800
    museum_max_inflight: int = 2
    museum_timeout: float = 100
    museum_corpus: Path = ROOT / "data/corpus.json"
