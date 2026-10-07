"""Local image retrieval. Similarity proposes candidates, never confirms identity.

Reference images are administrator-owned files with hashes and provenance. Uploaded
photos and their vectors live only for the duration of a request.
"""
from __future__ import annotations
import asyncio
import hashlib
import io
import json
import os
import tempfile
import zipfile
import threading
from pathlib import Path
import numpy as np
from PIL import Image, ImageOps

MODEL_ID = "facebook/dinov2-small"
MODEL_REVISION = "ed25f3a31f01632728cabb09d1542f84ab7b0056"
PREPROCESS_VERSION = "rgb-resize256-center224-global-and-half-overlap-v1"


def image_views(raw: bytes) -> list[Image.Image]:
    """Fixed whole + overlapping halves, independent of identity or evaluation labels."""
    with Image.open(io.BytesIO(raw)) as source:
        source.load()
        im = ImageOps.exif_transpose(source).convert("RGB")
    w, h = im.size
    boxes = [(0, 0, w, h), (0, 0, w, max(1, int(h * .65))), (0, int(h * .35), w, h),
             (0, 0, max(1, int(w * .65)), h), (int(w * .35), 0, w, h)]
    return [im.crop(box) for box in boxes]


class DinoEncoder:
    def __init__(self, model_dir: Path):
        # Optional dependencies: the default text-only deployment doesn't import torch.
        import torch
        from transformers import AutoModel
        if (model_dir / "revision.txt").read_text(encoding="utf-8").strip() != MODEL_REVISION:
            raise ValueError("Visual model revision mismatch; run scripts/download_visual_model.py")
        self.torch = torch
        self.model = AutoModel.from_pretrained(str(model_dir), local_files_only=True,
                                              trust_remote_code=False, use_safetensors=True).eval()
        self.lock = threading.Lock()

    def encode_patches(self, image: Image.Image) -> np.ndarray:
        """224-square image prepared by the local correspondence branch, no centre crop."""
        array = np.asarray(image, dtype=np.float32) / 255
        array = (array - np.array([.485, .456, .406], dtype=np.float32)) / np.array([.229, .224, .225], dtype=np.float32)
        with self.lock, self.torch.inference_mode():
            batch = self.torch.from_numpy(array.transpose(2, 0, 1)[None])
            values = self.model(pixel_values=batch).last_hidden_state[:, 1:].cpu().numpy()[0]
        return values / np.maximum(np.linalg.norm(values, axis=1, keepdims=True), 1e-12)

    def encode(self, images: list[Image.Image]) -> np.ndarray:
        pixels = []
        for image in images:
            w, h = image.size
            # Bound pathological aspect ratios before the shortest-edge resize.
            # This matches the centre discarded by the normal resize/crop anyway.
            if max(w, h) > 8 * min(w, h):
                side = min(w, h)
                x, y = (w - side) // 2, (h - side) // 2
                image = image.crop((x, y, x + side, y + side))
                w, h = image.size
            size = (int(w * 256 / min(w, h)), int(h * 256 / min(w, h)))
            image = image.resize(size, Image.Resampling.BICUBIC)
            x, y = (image.width - 224) // 2, (image.height - 224) // 2
            array = np.asarray(image.crop((x, y, x + 224, y + 224)), dtype=np.float32) / 255
            array = (array - np.array([.485, .456, .406], dtype=np.float32)) / np.array([.229, .224, .225], dtype=np.float32)
            pixels.append(array.transpose(2, 0, 1))
        vectors = []
        with self.lock, self.torch.inference_mode():
            for start in range(0, len(pixels), 8):
                batch = self.torch.from_numpy(np.stack(pixels[start:start + 8]))
                result = self.model(pixel_values=batch).last_hidden_state[:, 0].cpu().numpy()
                vectors.append(result)
        values = np.concatenate(vectors)
        return values / np.maximum(np.linalg.norm(values, axis=1, keepdims=True), 1e-12)


class MuseumVisualIndex:
    def __init__(self, manifest: Path, model_dir: Path, records: dict, encoder=None,
                 cache_dir: Path | None = None, cache_namespace: str | None = None):
        self.manifest, self.model_dir, self.records = manifest, model_dir, records
        self.encoder = encoder
        self.entries: list[dict] = []
        self.images: dict[str, bytes] = {}
        self.vectors = None
        self.index_hash = ""
        self.label_required_ids: set[str] = set()
        self.references_by_source: dict[str, list[dict]] = {}
        # Injected encoders must explicitly opt into their own cache namespace.
        self.cache_namespace = cache_namespace or (MODEL_REVISION if encoder is None else None)
        self.cache_dir = cache_dir if self.cache_namespace else None
        self.cache_stats = {"hits": 0, "misses": 0, "write_errors": 0}

    def _reference_vectors(self, clean: bytes) -> np.ndarray:
        key = hashlib.sha256(clean + str(self.cache_namespace).encode() + PREPROCESS_VERSION.encode()).hexdigest()
        path = self.cache_dir / (key + ".npz") if self.cache_dir else None
        if path and path.exists():
            try:
                with np.load(path, allow_pickle=False) as cached:
                    values = cached["vectors"]
                    valid = (values.ndim == 2 and values.shape[0] == 5 and values.shape[1] > 0
                             and (self.cache_namespace != MODEL_REVISION or values.shape[1] == 384)
                             and values.dtype == np.float32 and np.isfinite(values).all()
                             and np.allclose(np.linalg.norm(values, axis=1), 1, atol=1e-4)
                             and str(cached["key"]) == key
                             and str(cached["digest"]) == hashlib.sha256(values.tobytes()).hexdigest())
                    if valid:
                        self.cache_stats["hits"] += 1
                        return values
            except (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile):
                pass  # Corrupt/incomplete caches are disposable, never authoritative.
        self.cache_stats["misses"] += 1
        values = np.asarray(self.encoder.encode(image_views(clean)), dtype=np.float32)
        if values.ndim != 2 or values.shape[0] != 5 or not np.isfinite(values).all():
            raise ValueError("Invalid visual embeddings")
        if path:
            temporary = None
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as handle:
                    temporary = Path(handle.name)
                    np.savez(handle, vectors=values, key=key, digest=hashlib.sha256(values.tobytes()).hexdigest())
                os.replace(temporary, path)
            except OSError:
                self.cache_stats["write_errors"] += 1  # A read-only cache cannot break recognition.
            finally:
                if temporary and temporary.exists():
                    temporary.unlink(missing_ok=True)
        return values

    async def start(self):
        await asyncio.to_thread(self._start)

    def _start(self):
        from .vision import prepare_image
        raw_manifest = self.manifest.read_bytes()
        spec = json.loads(raw_manifest)
        if spec.get("version") != 1 or not spec.get("references"):
            raise ValueError("Invalid visual reference manifest")
        root = self.manifest.parent.resolve()
        seen = set()
        image_ids = []
        for item in spec["references"]:
            reference_id = item["id"]
            record = self.records.get(item["source_id"])
            if reference_id in seen or not record or record.get("status") != "active":
                raise ValueError("Duplicate reference or unknown/inactive source")
            if not item.get("source_url") or not item.get("license"):
                raise ValueError("Reference provenance required")
            if not isinstance(item.get("identity_requires_label", False), bool):
                raise ValueError("identity_requires_label must be a boolean")
            if item.get("identity_requires_label"):
                self.label_required_ids.add(item["source_id"])
            if item.get("view", "unspecified") not in {"whole", "detail", "unspecified"}:
                raise ValueError("Invalid reference view")
            path = (root / item["path"]).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Reference path outside manifest directory")
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != item["sha256"]:
                raise ValueError("Reference image hash mismatch")
            clean = prepare_image(raw)
            self.images[reference_id] = clean
            self.references_by_source.setdefault(item["source_id"], []).append(
                {"source_id": item["source_id"], "reference_id": reference_id, "view": item.get("view", "unspecified")})
            seen.add(reference_id)
            for _ in range(5):
                self.entries.append({"source_id": item["source_id"], "reference_id": reference_id})
            image_ids.append(reference_id)
        self.index_hash = hashlib.sha256(raw_manifest + MODEL_REVISION.encode() + PREPROCESS_VERSION.encode()).hexdigest()
        self.encoder = self.encoder or DinoEncoder(self.model_dir)
        # Encode one reference at a time, not thousands of full-resolution PIL crops.
        self.vectors = np.concatenate([self._reference_vectors(self.images[rid]) for rid in image_ids])
        if len(self.vectors) != len(self.entries) or not np.isfinite(self.vectors).all():
            raise ValueError("Invalid visual embeddings")

    async def search(self, raw: bytes, top_k: int = 3) -> list[dict]:
        return await asyncio.to_thread(self._search, raw, top_k)

    def _search(self, raw: bytes, top_k: int):
        query = self.encoder.encode(image_views(raw))
        scores = (query @ self.vectors.T).max(axis=0)
        # Max over viewpoints prevents works with more photos receiving extra votes.
        by_source = {}
        for entry, score in zip(self.entries, scores, strict=True):
            old = by_source.get(entry["source_id"])
            if old is None or score > old["score"]:
                by_source[entry["source_id"]] = {**entry, "score": round(float(score), 6)}
        # No probability/confidence is inferred from cosine scores. Model comparison
        # must reject out-of-gallery images even though nearest neighbours exist.
        return sorted(by_source.values(), key=lambda hit: (-hit["score"], hit["source_id"]))[:top_k]

    def reference_image(self, hit: dict) -> bytes:
        return self.images[hit["reference_id"]]

    def comparison_views(self, hits: list[dict]) -> list[dict]:
        """Opt-in verification experiment: same candidates, at most two photos each.

        Keep the retrieved reference first and prefer a complementary whole/detail
        reference. This does not rescore retrieval or synthesize an extra match vote.
        """
        expanded, seen = [], set()
        for hit in hits:
            sid = hit["source_id"]
            if sid in seen:
                continue
            seen.add(sid)
            expanded.append(hit)
            rows = self.references_by_source.get(sid, [])
            current = next((r for r in rows if r["reference_id"] == hit["reference_id"]), {})
            alternatives = [r for r in rows if r["reference_id"] != hit["reference_id"]
                            and self.images[r["reference_id"]] != self.images[hit["reference_id"]]]
            preferred = next((r for r in alternatives if r["view"] != current.get("view")), None)
            if preferred or alternatives:
                extra = preferred or alternatives[0]
                expanded.append({"source_id": sid, "reference_id": extra["reference_id"]})
            if len(seen) == 5:
                break
        return expanded

    def reference_hits(self, source_ids: list[str], limit: int = 2) -> list[dict]:
        """Resolve a bounded text-recalled set to actual images, without inventing scores."""
        hits = []
        if limit <= 0:
            return hits
        for source_id in dict.fromkeys(source_ids):
            rows = self.references_by_source.get(source_id, [])
            if rows:
                preferred = next((r for r in rows if r['view'] == 'whole'), rows[0])
                hits.append({"source_id": source_id, "reference_id": preferred["reference_id"]})
            if len(hits) >= limit:
                break
        return hits
