"""Explicit, pinned download. Runtime never downloads weights or remote code."""
from pathlib import Path
from huggingface_hub import snapshot_download

MODEL = "facebook/dinov2-small"
REVISION = "ed25f3a31f01632728cabb09d1542f84ab7b0056"
DEST = Path(__file__).resolve().parents[1] / "models/dinov2-small"

if __name__ == "__main__":
    snapshot_download(MODEL, revision=REVISION, local_dir=DEST,
                      allow_patterns=["config.json", "model.safetensors", "preprocessor_config.json", "README.md"])
    (DEST / "revision.txt").write_text(REVISION, encoding="utf-8")
    print(f"Downloaded {MODEL}@{REVISION} to {DEST}")
