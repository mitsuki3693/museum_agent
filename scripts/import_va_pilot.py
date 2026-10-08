"""Resumable, local-only V&A pilot. Never changes the active app configuration.

Run from the repository root with its Python environment. Downloaded content,
API snapshots and output manifests remain under data/private (gitignored).
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import time
import uuid
import httpx
from bs4 import BeautifulSoup
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "data/private"
TERMS = "https://www.vam.ac.uk/info/va-websites-terms-conditions"

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def atomic(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + "." + uuid.uuid4().hex + ".tmp")
    temp.write_bytes(raw)
    temp.replace(path)

def save(path, obj):
    atomic(path, json.dumps(obj, ensure_ascii=False, indent=2).encode())

def download(client, url, path):
    if path.exists():
        return path.read_bytes()
    response = client.get(url)
    response.raise_for_status()
    atomic(path, response.content)
    time.sleep(.15)
    return response.content

def text(value):
    return BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)

def make_record(oid, raw, image_id, image, folder):
    r = json.loads(raw)["record"]
    url = f"https://collections.vam.ac.uk/item/{oid}/"
    maker = "; ".join(x.get("name", {}).get("text", "") for x in
                      r.get("artistMakerPerson", []) + r.get("artistMakerOrganisations", []))
    title = r.get("objectType") or "Collection object"
    lines = [f"Title: {title}", f"Museum number: {r.get('accessionNumber', '')}", f"Maker: {maker}",
             "Date: " + "; ".join(x.get("date", {}).get("text", "") for x in r.get("productionDates", [])),
             "Place: " + "; ".join(x.get("place", {}).get("text", "") for x in r.get("placesOfOrigin", []))]
    for key in ("materialsAndTechniques", "briefDescription", "summaryDescription", "physicalDescription", "objectHistory"):
        if r.get(key):
            lines.append(f"{key}: {text(r[key])}")
    content = "\n".join(lines)
    media = dict(path=f"{folder}/images/{image_id}.jpg", sha256=sha(image), image_id=image_id,
                 source_url=f"https://framemark.vam.ac.uk/collections/{image_id}/full/!800,800/0/default.jpg",
                 source_page=url, license="V&A website terms; local private study only", license_url=TERMS,
                 attribution="Victoria and Albert Museum, London")
    sid = "va-" + oid.lower()
    record = dict(_id=sid, title=title, content=content, source_hash=sha(content.encode()), status="active",
                  source_url=url, api_url=f"https://api.vam.ac.uk/v2/museumobject/{oid}",
                  fetched_at=datetime.now(timezone.utc).isoformat(), source_updated_at=r.get("recordModificationDate"),
                  source_kind="collection_record", collection="V&A",
                  fields=dict(system_number=oid, accession_number=r.get("accessionNumber", ""), artist_display=maker),
                  license=media["license"], license_url=TERMS, attribution=media["attribution"], local_image=media["path"],
                  image_provenance=media, source_snapshot=dict(path=f"{folder}/records/{oid}.json", sha256=sha(raw)),
                  curation=dict(human_reviewed=False, gallery_locations_current=False,
                                scope="catalogue record, not official audio; display location not verified"))
    return record, dict(id=sid + "-primary", source_id=sid, view="unspecified", **media)


def fetch_candidate(client, oid, category, folder, folder_name, image_ids):
    """Network work only; the caller commits results in search-result order."""
    try:
        raw = download(client, f"https://api.vam.ac.uk/v2/museumobject/{oid}", folder / "records" / (oid + ".json"))
        r = json.loads(raw)["record"]
        if category not in r.get("objectType", "").lower():
            return None, None
        candidates = [v for v in r.get("images", []) if v not in image_ids]
        if not candidates:
            return None, None
        image_id = candidates[0]
        image = download(client, f"https://framemark.vam.ac.uk/collections/{image_id}/full/!800,800/0/default.jpg",
                         folder / "images" / (image_id + ".jpg"))
        with Image.open(io.BytesIO(image)) as im:
            im.verify()
        return make_record(oid, raw, image_id, image, folder_name), None
    except (httpx.HTTPError, ValueError, KeyError, OSError) as exc:
        return None, dict(id=oid, error=type(exc).__name__)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=int, default=100)
    parser.add_argument("--folder", default="va-pilot-100-v1")
    parser.add_argument("--base-corpus", default="va-collection-v6-blue-release.json")
    parser.add_argument("--base-references", default="visual-references-v7-blue-release.json")
    parser.add_argument("--max-pages", type=int, default=4)
    parser.add_argument("--categories", default="vase,jug,plate,sculpture,bust")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", args.folder) or not 1 <= args.target <= 1000:
        parser.error("Use a simple folder name and a target between 1 and 1000")
    categories = tuple(c.strip() for c in args.categories.split(',') if c.strip())
    if not 1 <= args.max_pages <= 20 or not categories or any(not re.fullmatch(r'[a-z ]+', c) for c in categories):
        parser.error("Use 1-20 pages and comma-separated English object types")
    if not 1 <= args.workers <= 4:
        parser.error("Use 1-4 download workers")
    folder = PRIVATE / args.folder
    corpus_path = PRIVATE / (args.folder + "-corpus.json")
    refs_path = PRIVATE / (args.folder + "-references.json")
    if corpus_path.exists() or refs_path.exists():
        parser.error("Completed output snapshot already exists; choose a new --folder to preserve evaluation versions")
    base = PRIVATE / args.base_corpus
    base_refs = PRIVATE / args.base_references
    if not base.resolve().is_relative_to(PRIVATE.resolve()) or not base_refs.resolve().is_relative_to(PRIVATE.resolve()):
        parser.error("All data must stay under data/private")
    records = json.loads(base.read_bytes())
    if args.target <= len(records):
        parser.error("Target must exceed the number of records in the base corpus")
    references = json.loads(base_refs.read_bytes())
    seen = {r.get("fields", {}).get("system_number", "") for r in records}
    seen.update(re.findall(r"/item/(O\d+)", " ".join(r["source_url"] for r in records)))
    seen_ids = {r["_id"] for r in records}
    image_ids = {r.get("image_id") for r in references["references"]}
    image_hashes = {r["sha256"] for r in references["references"]}
    # Ambiguous shared image of paired works; never import the OOD test objects.
    seen.add("O70700")
    failures = []
    with httpx.Client(timeout=45, follow_redirects=True) as client, ThreadPoolExecutor(max_workers=args.workers) as pool:
        for category_index, category in enumerate(categories):
            # Redistribute unfilled categories. A fixed quota can stop short even
            # when the final category has enough usable public records.
            remaining_categories = len(categories) - category_index
            quota = (args.target - len(records) + remaining_categories - 1) // remaining_categories
            category_start = len(records)
            for page in range(1, args.max_pages + 1):
                params = dict(q_object_type=category, images_exist=1, page_size=100, page=page)
                if category in {"vase", "jug", "plate"}:
                    params["id_place"] = "x29383"  # Delft; structured filter, not a broad OR query.
                request_url = str(httpx.URL("https://api.vam.ac.uk/v2/objects/search", params=params))
                result = json.loads(download(client, request_url, folder / f"search-{category}-{page}.json"))
                pending = []
                for item in result.get('records', []):
                    oid = item['systemNumber']
                    if oid not in seen and 'va-' + oid.lower() not in seen_ids:
                        seen.add(oid)
                        pending.append(oid)
                while pending and len(records) < args.target and len(records) - category_start < quota:
                    take = min(args.workers, args.target - len(records), quota - (len(records) - category_start))
                    batch, pending = pending[:take], pending[take:]
                    futures = [pool.submit(fetch_candidate, client, oid, category, folder, args.folder, frozenset(image_ids)) for oid in batch]
                    for future in futures:
                        pair, failure = future.result()
                        if failure:
                            failures.append(failure)
                        if pair is None:
                            continue
                        record, reference = pair
                        if reference['image_id'] in image_ids or reference['sha256'] in image_hashes:
                            continue
                        records.append(record)
                        references['references'].append(reference)
                        seen_ids.add(record['_id'])
                        image_ids.add(reference['image_id'])
                        image_hashes.add(reference['sha256'])
                        print(json.dumps(dict(count=len(records), added=record['_id'], category=category)), flush=True)
                    if len(records) >= args.target or len(records) - category_start >= quota:
                        break
                if len(records) >= args.target or len(records) - category_start >= quota or not result.get("records"):
                    break
            if len(records) >= args.target:
                break
    report = dict(target=args.target, actual=len(records), references=len(references["references"]), failures=failures,
                  categories=categories, max_pages=args.max_pages, workers=args.workers,
                  base_corpus_sha256=sha(base.read_bytes()), base_references_sha256=sha(base_refs.read_bytes()),
                  built_at=datetime.now(timezone.utc).isoformat(), active_configuration_changed=False)
    # Only completed snapshots may become candidate app configuration.
    save(folder / "import-report.json", report)
    if len(records) < args.target:
        raise SystemExit(f"Only {len(records)} records collected; cached files retained for resume")
    save(corpus_path, records)
    save(refs_path, references)
    print(json.dumps(report), flush=True)

if __name__ == "__main__":
    main()
