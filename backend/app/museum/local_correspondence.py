"""Diagnostic, asymmetric patch matching. Scores never establish artwork identity."""
import asyncio
import hashlib
import io
import os
from pathlib import Path
import tempfile
import zipfile
import numpy as np
from PIL import Image, ImageOps
from .visual_index import MODEL_REVISION

VERSION = "dinov2-patches-letterbox224-mutual-affine-v1"
GRID = 16

def patch_input(raw: bytes, roi=None):
    with Image.open(io.BytesIO(raw)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    w, h = image.size
    scale = min(224 / w, 224 / h)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    x, y = (224 - nw) // 2, (224 - nh) // 2
    square = Image.new("RGB", (224, 224), (124, 116, 104))
    square.paste(image.resize((nw, nh), Image.Resampling.BICUBIC), (x, y))
    grid_y, grid_x = np.mgrid[:16, :16]
    # Ignore any patch containing padding; coordinates map back to the uncropped image.
    valid = ((grid_x*14 >= x) & ((grid_x+1)*14 <= x+nw) & (grid_y*14 >= y) & ((grid_y+1)*14 <= y+nh)).reshape(-1)
    coords = np.stack([((grid_x+.5)*14-x)/nw, ((grid_y+.5)*14-y)/nh], axis=-1).reshape(-1, 2)
    if roi is not None:
        a = np.asarray(roi, dtype=float)
        if a.shape != (4,) or not np.isfinite(a).all() or not (0 <= a[0] < a[2] <= 1 and 0 <= a[1] < a[3] <= 1):
            raise ValueError("ROI must be a normalized nonempty box")
        valid &= (coords[:, 0] >= a[0]) & (coords[:, 0] <= a[2]) & (coords[:, 1] >= a[1]) & (coords[:, 1] <= a[3])
    return square, coords, valid, (14/nw, 14/nh)

def correspond(query, reference, qcoords, rcoords, qvalid, rvalid):
    """Mutual neighbours + ambiguity margin + deterministic local affine consensus.

    Thresholds are fixed experimental settings, not calibrated identity confidence.
    Failure to establish 2D correspondence is not evidence of different 3D objects.
    """
    qi, ri = np.flatnonzero(qvalid), np.flatnonzero(rvalid)
    empty = dict(coverage=0., match_count=0, mutual_count=0, median_similarity=0., pairs=[])
    if len(qi) < 4 or len(ri) < 4:
        return empty
    sim = query[qi] @ reference[ri].T
    forward, backward = sim.argmax(1), sim.argmax(0)
    top = np.take_along_axis(sim, forward[:, None], 1)[:, 0]
    second = np.partition(sim, -2, axis=1)[:, -2]
    keep = (backward[forward] == np.arange(len(qi))) & (top >= .72) & ((top-second) >= .015)
    qidx, ridx, scores = qi[keep], ri[forward[keep]], top[keep]
    if len(qidx) < 4:
        return {**empty, "mutual_count": len(qidx)}
    a, b = np.column_stack([qcoords[qidx], np.ones(len(qidx))]), rcoords[ridx]
    best = np.zeros(len(qidx), dtype=bool)
    rng = np.random.default_rng(17)
    for _ in range(128):
        sample = rng.choice(len(qidx), 3, replace=False)
        if abs(np.linalg.det(a[sample])) < 1e-4:
            continue
        transform = np.linalg.solve(a[sample], b[sample])
        if abs(np.linalg.det(transform[:2])) < .005:
            continue
        inliers = np.linalg.norm(a @ transform-b, axis=1) < .065
        if inliers.sum() > best.sum():
            best = inliers
    if best.sum() < 4:
        return {**empty, "mutual_count": len(qidx)}
    pairs = [dict(query=[round(float(v), 4) for v in qcoords[q]], reference=[round(float(v), 4) for v in rcoords[r]],
                  similarity=round(float(s), 4)) for q, r, s in zip(qidx[best], ridx[best], scores[best])]
    return dict(coverage=round(len(pairs)/len(qi), 4), match_count=len(pairs), mutual_count=len(qidx),
                median_similarity=round(float(np.median(scores[best])), 4), pairs=pairs)

class LocalCorrespondence:
    def __init__(self, visual, cache_dir: Path):
        self.visual = visual
        self.cache_dir = cache_dir / "patches-v1"
        self.references = {}
        self.cache_stats = dict(hits=0, misses=0, write_errors=0)

    def _encode_reference(self, rid):
        if rid in self.references:
            return self.references[rid]
        raw = self.visual.images[rid]
        square, coords, valid, cell = patch_input(raw)
        key = hashlib.sha256(raw + MODEL_REVISION.encode() + VERSION.encode()).hexdigest()
        path = self.cache_dir / (key + ".npz")
        values = None
        try:
            with np.load(path, allow_pickle=False) as data:
                a = data["vectors"]
                if (a.shape == (256, 384) and a.dtype == np.float32 and np.isfinite(a).all()
                    and np.allclose(np.linalg.norm(a, axis=1), 1, atol=1e-4)
                    and str(data["key"]) == key
                    and str(data["digest"]) == hashlib.sha256(a.tobytes()).hexdigest()):
                    values = a
                    self.cache_stats["hits"] += 1
        except (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile):
            pass
        if values is None:
            self.cache_stats["misses"] += 1
            values = np.asarray(self.visual.encoder.encode_patches(square), dtype=np.float32)
            if values.shape != (256, 384) or not np.isfinite(values).all():
                raise ValueError("Invalid patch feature output")
            temporary = None
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, suffix=".tmp") as handle:
                    temporary = Path(handle.name)
                    np.savez(handle, vectors=values, key=key, digest=hashlib.sha256(values.tobytes()).hexdigest())
                os.replace(temporary, path)
            except OSError:
                self.cache_stats["write_errors"] += 1
            finally:
                if temporary:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass  # Cleanup failure must not turn disposable cache into an outage.
        self.references[rid] = (values, coords, valid, cell)
        return self.references[rid]

    async def select(self, raw, hits, roi=None):
        return await asyncio.to_thread(self._select, raw, hits, roi)

    def _select(self, raw, hits, roi=None):
        square, qc, qv, _ = patch_input(raw, roi)
        q = self.visual.encoder.encode_patches(square)  # Visitor vectors never persisted.
        selected, reports = [], []
        for hit in hits[:5]:
            candidates = []
            for row in self.visual.references_by_source.get(hit["source_id"], []):
                rid = row["reference_id"]
                r, rc, rv, cell = self._encode_reference(rid)
                match = correspond(q, r, qc, rc, qv, rv)
                box = None
                if match["pairs"]:
                    xy = np.array([p["reference"] for p in match["pairs"]])
                    low, high = np.maximum(0, xy.min(0)-cell), np.minimum(1, xy.max(0)+cell)
                    box = [round(float(v), 4) for v in [*low, *high]]
                candidates.append(dict(reference_id=rid, reference_box=box, **match))
            # With no geometric support retain the original reference, not arbitrary first.
            candidates.sort(key=lambda r: (r["match_count"], r["median_similarity"], r["reference_id"]==hit["reference_id"]), reverse=True)
            best = candidates[0] if candidates else dict(reference_id=hit["reference_id"], reference_box=None, **dict(coverage=0., match_count=0, pairs=[]))
            selected.append({**hit, "reference_id": best["reference_id"]})
            reports.append(dict(source_id=hit["source_id"], version=VERSION, coverage_scope="manual_roi" if roi else "whole_image_excluding_padding",
                                foreground_verified=False, views=candidates, **best))
        return selected, reports

    def crop_reference(self, hit, box):
        with Image.open(io.BytesIO(self.visual.reference_image(hit))) as image:
            w, h = image.size
            output = io.BytesIO()
            image.crop((int(box[0]*w), int(box[1]*h), max(int(box[2]*w), int(box[0]*w)+1), max(int(box[3]*h), int(box[1]*h)+1))).save(output, format="JPEG", quality=85)
            return output.getvalue()
