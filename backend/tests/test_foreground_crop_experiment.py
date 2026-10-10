"""Guard destructive crop proposals without requiring the optional OpenCV worker."""
import importlib.util
from pathlib import Path
import numpy as np
from PIL import Image
import pytest

spec = importlib.util.spec_from_file_location('foreground_crop_experiment',
    Path(__file__).resolve().parents[2]/'scripts/foreground_crop.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_crop_keeps_entire_component_and_adds_margin():
    labels = np.zeros((100, 100), dtype=np.int32)
    labels[20:80, 35:65] = 1
    proposal = module.mask_box(labels)
    assert proposal['applied'] and proposal['box'] == [.31, .16, .69, .84]
    pixels = np.arange(100*100*3, dtype=np.uint8).reshape(100, 100, 3)
    crop = module.crop_image(Image.fromarray(pixels), proposal)
    assert np.array_equal(np.asarray(crop), pixels[16:84, 31:69])


@pytest.mark.parametrize('kind', ['empty', 'tiny', 'full', 'edge', 'disconnected'])
def test_ambiguous_or_incomplete_subject_keeps_original_image(kind):
    labels = np.zeros((100, 100), dtype=np.int32)
    if kind == 'tiny': labels[40:45, 40:45] = 1
    elif kind == 'full': labels[:] = 1
    elif kind == 'edge': labels[1:85, 20:60] = 1
    elif kind == 'disconnected':
        labels[20:40, 20:40] = 1
        labels[60:80, 60:80] = 2
    proposal = module.mask_box(labels)
    assert not proposal['applied']
    original = Image.new('RGB', (100, 100), (15, 23, 49))
    cropped = module.crop_image(original, proposal)
    assert cropped.size == original.size and cropped.tobytes() == original.tobytes()


@pytest.mark.parametrize('box', [[-1, 0, 1, 1], [0, 0, float('nan'), 1], [1, 0, .5, 1]])
def test_invalid_box_cannot_silently_truncate_subject(box):
    with pytest.raises(ValueError):
        module.crop_image(Image.new('RGB', (30, 30)), {'box': box, 'applied': True})
