"""Rebuild the frozen synthetic text cards on Windows; no model calls."""
import hashlib
import io
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from app.museum.config import ROOT


def main():
    font_path = Path("C:/Windows/Fonts/arial.ttf")
    if not font_path.exists():
        raise SystemExit("These frozen cards use Windows Arial. Do not silently substitute a font.")
    font = ImageFont.truetype(str(font_path), 46)
    cases = json.loads((ROOT / "eval/synthetic-photo-cases.json").read_text(encoding="utf-8"))
    for case in cases:
        image = Image.new("RGB", (1100, 650), "white")
        ImageDraw.Draw(image).multiline_text((60, 80), case["text"], font=font, fill="black", spacing=30)
        output = io.BytesIO()
        image.save(output, format="PNG")
        raw = output.getvalue()
        if hashlib.sha256(raw).hexdigest() != case["sha256"]:
            raise SystemExit(f"Rendering changed for {case['id']}; review a new dataset version first.")
        path = ROOT / case["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    print("Four frozen synthetic cards prepared; not real visitor photographs.")


if __name__ == "__main__":
    main()
