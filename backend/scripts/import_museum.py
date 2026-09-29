"""Download a small, reproducible Met CC0 metadata corpus; never download images."""
from __future__ import annotations
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[2]
IDS = [436535, 437133, 436532, 437853, 437881, 437397, 436105, 436121,
       544498, 544442, 544449, 544887, 469960, 459055, 45434, 44734]
FIELDS = {"title": "名称 Title", "objectID": "藏品编号 Object ID", "objectName": "类型 Type",
          "artistDisplayName": "作者 Artist", "artistDisplayBio": "作者简介 Artist biography",
          "objectDate": "年代 Date", "medium": "材质 Medium", "dimensions": "尺寸 Dimensions",
          "culture": "文化 Culture", "period": "时期 Period", "dynasty": "朝代 Dynasty",
          "department": "馆藏部门 Department", "creditLine": "入藏信息 Credit line"}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", nargs="*", type=int, default=IDS)
    args = parser.parse_args()
    target = ROOT / "data"
    (target / "raw").mkdir(parents=True, exist_ok=True)
    records = []
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        for object_id in args.ids:
            url = f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{object_id}"
            response = client.get(url)
            response.raise_for_status()
            raw = response.json()
            if not raw.get("title") or raw.get("objectID") != object_id:
                raise ValueError(f"Invalid collection record {object_id}")
            # Whitelist metadata; image URLs are intentionally not imported.
            selected = {k: raw.get(k, "") for k in FIELDS}
            selected["objectURL"] = raw.get("objectURL")
            selected["metadataDate"] = raw.get("metadataDate")
            raw_text = json.dumps(selected, ensure_ascii=False, sort_keys=True, indent=2)
            (target / "raw" / f"met-{object_id}.json").write_text(raw_text, encoding="utf-8")
            content = "\n".join(f"{label}: {selected[k]}" for k, label in FIELDS.items() if selected.get(k))
            records.append({"_id": f"met-{object_id}", "title": raw["title"], "content": content,
                            "fields": selected, "source_url": raw.get("objectURL") or url,
                            "api_url": url, "license": "CC0-1.0", "status": "active",
                            "fetched_at": datetime.now(timezone.utc).isoformat(),
                            "source_updated_at": raw.get("metadataDate"),
                            "source_hash": hashlib.sha256(raw_text.encode()).hexdigest()})
            print(f"Imported met-{object_id}: {raw['title']}")
    # Publish the corpus atomically: failed fetches never replace a working snapshot.
    pending = target / "corpus.pending.json"
    pending.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    pending.replace(target / "corpus.json")
    print(f"Published {len(records)} records. This is a static snapshot, not current gallery availability.")

if __name__ == "__main__":
    main()
