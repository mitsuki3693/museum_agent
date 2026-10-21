"""One paid provider call, using only a public sample question; no credentials needed."""
import json
import time
import urllib.request

body = {"workId": "artic-16568", "question": "请用两句话给孩子介绍这幅画。", "style": "children", "history": []}
request = urllib.request.Request(
    "https://muse-public-ai.muse-public-api.workers.dev/chat",
    data=json.dumps(body).encode(),
    headers={"Origin": "https://mitsuki3693.github.io", "Content-Type": "application/json"},
)
started = time.monotonic()
with urllib.request.urlopen(request, timeout=40) as response:
    result = json.load(response)
    assert response.status == 200
assert result["kind"] == "ai" and result["workId"] == body["workId"] and result["answer"].strip()
assert result["source"] == "https://www.artic.edu/artworks/16568"
print(json.dumps({"status": "passed", "seconds": round(time.monotonic()-started, 2), **result}, ensure_ascii=False))
