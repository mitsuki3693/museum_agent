"""Protect the one-reference diagnostic from accidental corpus changes."""
from pathlib import Path
import copy
import pytest


@pytest.fixture
def check(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    # backend/tests -> repository is parents[2]. Import does not execute main.
    from evaluate_neptune_view import assert_single_addition
    return assert_single_addition


def manifests():
    base = {"references": [{"id": "existing", "source_id": "other", "path": "old.jpg"}]}
    new = copy.deepcopy(base)
    new["references"].append({"id": "va-neptune-archival-back-v1", "source_id": "va-neptune-triton"})
    return base, new


def test_one_new_neptune_reference_is_accepted(check):
    check(*manifests())


@pytest.mark.parametrize("change", ["old_path", "remove", "extra", "duplicate", "wrong_work"])
def test_uncontrolled_reference_changes_are_rejected(check, change):
    base, new = manifests()
    if change == "old_path": new["references"][0]["path"] = "different.jpg"
    if change == "remove": new["references"].pop(0)
    if change == "extra": new["references"].append({"id": "extra", "source_id": "another"})
    if change == "duplicate": new["references"].append(copy.deepcopy(new["references"][0]))
    if change == "wrong_work": new["references"][-1]["source_id"] = "another"
    with pytest.raises(AssertionError): check(base, new)
