"""Turn visual evidence into bounded suggestions; similarity is never identity proof."""
from __future__ import annotations

import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

POLICY_VERSION = "photo-policy-v3-reference-and-display-boundaries"

SHARED = {
    "blue_white": "蓝白装饰", "tiered": "多层塔形结构", "spouts": "多个插花口",
    "figures": "人物组合", "pose": "相近的人物姿态", "outline": "相近轮廓",
    "decoration": "相近装饰", "color": "相近色彩", "composition": "相近构图",
}
STEPS = {
    "label": "补拍旁边的展签，尽量拍清作品名称或编号。",
    "whole": "补拍完整作品，让顶部和底座都进入画面。",
    "base": "补拍底座的图案和支撑部分。",
    "top": "补拍顶部装饰或人物头部。",
    "angle": "换一个角度，拍清人物姿态和部件的位置。",
}

class Feature(BaseModel):
    model_config = ConfigDict(extra="forbid")
    part: Literal["outline", "top", "base", "decoration", "pose", "parts", "inscription"]
    query_detail: str = Field(max_length=180)
    reference_detail: str = Field(max_length=180)
    relation: Literal["match", "different", "not_visible"]
    distinctive: StrictBool

class Comparison(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str = Field(min_length=1, max_length=100)
    identity: Literal["same_work", "uncertain", "different_work"]
    features: list[Feature] = Field(default_factory=list, max_length=7)
    shared_features: list[Literal["blue_white", "tiered", "spouts", "figures", "pose", "outline", "decoration", "color", "composition"]] = Field(default_factory=list, max_length=3)
    needs: list[Literal["label", "whole", "base", "top", "angle"]] = Field(default_factory=list, max_length=3)

    @field_validator('shared_features', 'needs', mode='before')
    @classmethod
    def bounded_display_tags(cls, value, info):
        # These are optional UI hints, not identity evidence. Unknown hints can be
        # omitted; required identity and structural comparison fields stay strict.
        if isinstance(value, list):
            allowed = SHARED if info.field_name == 'shared_features' else STEPS
            return list(dict.fromkeys(v for v in value if isinstance(v, str) and v in allowed))[:3]
        return value

class Comparisons(BaseModel):
    model_config = ConfigDict(extra="forbid")
    comparisons: list[Comparison] = Field(default_factory=list, max_length=8)

def label_support(source: dict, visible_text: str) -> bool:
    """Only a catalogue accession read from the query label, never a guessed title."""
    number = source.get("fields", {}).get("accession_number", "")
    parts = re.findall(r"[A-Za-z]+|[0-9]+", number)
    if len("".join(parts)) < 7:
        return False
    pattern = r"(?<![A-Za-z0-9])" + r"[\W_]*".join(re.escape(p) for p in parts) + r"(?![A-Za-z0-9])"
    return re.search(pattern, visible_text, re.IGNORECASE) is not None

def decide(comparisons: Comparisons, sources: list[dict], visual_hits: list[dict], visible_text: str,
           label_required_ids: set[str] | None = None):
    allowed = {s["_id"]: s for s in sources}
    ids = [c.candidate_id for c in comparisons.comparisons]
    if len(ids) != len(set(ids)) or any(i not in allowed for i in ids):
        raise ValueError("Unknown or duplicate comparison candidate")
    scores = {h["source_id"]: h.get("score", -1.0) for h in visual_hits}
    decisions, likely, uncertain, similar, needs = [], [], [], [], []
    for item in comparisons.comparisons:
        # Independent anatomical/structural areas, not repeated generic adjectives.
        matches = {f.part for f in item.features if f.relation == "match" and f.distinctive
                   and f.query_detail.strip() and f.reference_detail.strip()}
        differences = {f.part for f in item.features if f.relation == "different"}
        label = label_support(allowed[item.candidate_id], visible_text)
        has_reference = item.candidate_id in scores
        if item.candidate_id in (label_required_ids or set()) and not label:
            # Curated lookalike groups need extra evidence, even if the VLM says same_work.
            # This is an explicit conservative policy, not improved fine-grained recognition.
            tier, reason = "similar_only", "lookalike_requires_label"
            needs.append("label")
        elif differences or item.identity == "different_work":
            tier, reason = "similar_only", "visible_difference"
        elif item.identity == "same_work" and (label or (has_reference and len(matches) >= 2)):
            tier, reason = "likely_match", "label_or_distinctive_agreement"
            likely.append(item)
        elif item.identity in ("same_work", "uncertain") and has_reference and matches:
            tier, reason = "uncertain", "partial_distinctive_agreement"
            uncertain.append(item)
        else:
            tier, reason = "similar_only", "identity_evidence_insufficient"
        if item.shared_features:
            similar.append(item)
        needs.extend(item.needs)
        # Retain codes/counts, not OCR or free-form image/label descriptions.
        decisions.append({"candidate_id": item.candidate_id, "model_identity": item.identity,
                          "tier": tier, "reason": reason, "matching_parts": sorted(matches),
                          "different_parts": sorted(differences), "label_supported": label,
                          "shared_features": item.shared_features, "needs": item.needs})
    order = lambda item: (-scores.get(item.candidate_id, -1), ids.index(item.candidate_id))
    def card(item):
        source = allowed[item.candidate_id]
        return {"id": item.candidate_id, "title": source["title"], "source_url": source.get("source_url", ""),
                "artist": source.get("fields", {}).get("artist_display", ""),
                "shared_features": [SHARED[k] for k in dict.fromkeys(item.shared_features)]}
    if likely:
        selected = sorted(likely + uncertain, key=order)[:3]
        # Never hide a competing plausible identity under a single confident label.
        state = "likely_match" if len(selected) == 1 else "uncertain"
    elif uncertain:
        selected, state = sorted(uncertain, key=order)[:3], "uncertain"
    else:
        selected, state = [], "no_reliable_match"
    messages = {
        "likely_match": "找到了有具体特征支持的候选。请对照图片，确认是不是你面前这件。",
        "uncertain": "有些细节相似，但还不足以确认。可以对照候选，或补拍关键部位。",
        "no_reliable_match": "暂时没有在当前馆藏图库中找到足够可靠的匹配。",
    }
    # A generic outline alone must not beat several meaningful shared properties.
    recommendations = sorted(similar, key=lambda item: (-len(set(item.shared_features)), *order(item)))[:1] if not selected else []
    steps = list(dict.fromkeys(needs + ["whole", "label"]))[:2]
    result = {"status": "needs_confirmation" if selected else "not_matched", "match_state": state,
              "message": messages[state], "candidates": [card(c) for c in selected],
              "similar_candidates": [card(c) for c in recommendations],
              "next_steps": [STEPS[k] for k in steps], "confirmation_required": True,
              "identity_confirmed": False}
    return result, decisions
