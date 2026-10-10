"""Offline foreground-box experiment. Not an identity or segmentation service.

OpenCV is optional and only imported by propose_box; serving dependencies remain
unchanged. A fixed near-full rectangle initializes GrabCut. Its mask proposes a
bounding box, never removes pixels inside that box or synthesizes unseen parts.
"""
import math
import numpy as np

VERSION = 'grabcut-box320-inset1-iter5-margin4-v1'
PARAMETERS = dict(max_side=320, inset_fraction=.01, iterations=5, seed=17,
                  min_area_fraction=.03, max_area_fraction=.90,
                  min_largest_share=.80, margin_fraction=.04,
                  max_crop_area_fraction=.95)


def mask_box(labels, parameters=PARAMETERS):
    """Validate geometry before converting a segmentation into a crop proposal."""
    labels = np.asarray(labels)
    if labels.ndim != 2 or min(labels.shape) < 8 or labels.dtype.kind not in 'iu' or (labels < 0).any():
        raise ValueError('Expected nonnegative component labels of at least 8 pixels')
    a = labels > 0
    h, w = a.shape
    whole = [0., 0., 1., 1.]
    fraction = float(a.mean())
    def reject(reason, **extra):
        return dict(box=whole, applied=False, reason=reason,
                    foreground_fraction=round(fraction, 6), **extra)
    if not parameters['min_area_fraction'] <= fraction <= parameters['max_area_fraction']:
        return reject('foreground_area_out_of_range')
    areas = np.bincount(labels.ravel())[1:]
    count = np.count_nonzero(areas)
    largest = int(np.argmax(areas))+1
    share = float(areas[largest-1]/areas.sum())
    if share < parameters['min_largest_share']:
        return reject('multiple_foreground_components', largest_share=round(share, 6))
    y, x = np.nonzero(labels == largest)
    # A component touching the rectangle edge can be a truncated object. Do not
    # silently crop it further. This is geometric caution, not proof of quality.
    ix, iy = max(1, round(w*parameters['inset_fraction'])), max(1, round(h*parameters['inset_fraction']))
    if x.min() <= ix or y.min() <= iy or x.max() >= w-ix-1 or y.max() >= h-iy-1:
        return reject('foreground_reaches_initial_rectangle', largest_share=round(share, 6))
    mx, my = math.ceil(w*parameters['margin_fraction']), math.ceil(h*parameters['margin_fraction'])
    left, top = max(0, int(x.min())-mx), max(0, int(y.min())-my)
    right, bottom = min(w, int(x.max())+1+mx), min(h, int(y.max())+1+my)
    area = (right-left)*(bottom-top)/(w*h)
    if area >= parameters['max_crop_area_fraction']:
        return reject('crop_has_negligible_effect', largest_share=round(share, 6))
    return dict(box=[left/w, top/h, right/w, bottom/h], applied=True, reason='foreground_box',
                foreground_fraction=round(fraction, 6), largest_share=round(share, 6),
                crop_area_fraction=round(area, 6), components=int(count))


def propose_box(image):
    import cv2
    from PIL import Image
    thumb = image.copy().convert('RGB')
    thumb.thumbnail((PARAMETERS['max_side'],)*2, Image.Resampling.BICUBIC)
    a = np.asarray(thumb)
    h, w = a.shape[:2]
    if min(h, w) < 8:
        return dict(box=[0., 0., 1., 1.], applied=False, reason='image_too_small')
    ix, iy = max(1, round(w*.01)), max(1, round(h*.01))
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.setNumThreads(1)
    cv2.setRNGSeed(PARAMETERS['seed'])
    try:
        cv2.grabCut(a, mask, (ix, iy, w-2*ix, h-2*iy),
                    np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64),
                    PARAMETERS['iterations'], cv2.GC_INIT_WITH_RECT)
    except cv2.error:
        return dict(box=[0., 0., 1., 1.], applied=False, reason='segmentation_error')
    foreground = ((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD)).astype(np.uint8)
    _, labels = cv2.connectedComponents(foreground, connectivity=8)
    return mask_box(labels)


def crop_image(image, proposal):
    box = proposal['box']
    if len(box) != 4 or not all(math.isfinite(x) for x in box) or not (
        0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
        raise ValueError('Invalid normalized crop box')
    if not proposal['applied']:
        return image.copy()
    w, h = image.size
    return image.crop((math.floor(box[0]*w), math.floor(box[1]*h),
                       math.ceil(box[2]*w), math.ceil(box[3]*h)))
