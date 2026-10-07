"""Background points on either side must not contribute subject evidence."""
import importlib.util
from pathlib import Path
import sys

scripts = Path(__file__).resolve().parents[2]/'scripts'
sys.path.insert(0, str(scripts))
try:
    spec = importlib.util.spec_from_file_location('subject_region_experiment', scripts/'evaluate_subject_regions.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
finally:
    sys.path.remove(str(scripts))


def test_both_endpoints_must_be_inside_independently_scaled_subjects():
    polygon = [[.2,.1],[.8,.1],[.8,.6],[.2,.6]]
    args = ([100,200], [200,100], polygon, polygon)
    assert module.subject_pair([50,80], [100,40], *args)
    assert not module.subject_pair([50,180], [100,40], *args)
    assert not module.subject_pair([50,80], [100,90], *args)


def test_polygon_not_bounding_box_excludes_background_corner():
    polygon = [[.5,.1],[.9,.9],[.1,.9]]
    assert module.inside([.5,.5], polygon)
    assert not module.inside([.15,.15], polygon)
