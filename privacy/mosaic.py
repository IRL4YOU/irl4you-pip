import cv2
import numpy as np


def grow(box, margin, px, W, H):
    w, h = box[2] - box[0], box[3] - box[1]
    return (int(max(0, box[0] - margin * w - px)), int(max(0, box[1] - margin * h - px)), int(min(W, box[2] + margin * w + px)), int(min(H, box[3] + margin * h + px)))


def pixelate(img, box, cells_short=3):
    x1, y1, x2, y2 = box
    if x2 - x1 < 2 or y2 - y1 < 2:
        return
    roi = img[y1:y2, x1:x2]
    h, w = roi.shape[:2]
    blk = max(3, int(min(w, h) / cells_short))
    small = cv2.resize(roi, (max(1, w // blk), max(1, h // blk)), interpolation=cv2.INTER_AREA)
    img[y1:y2, x1:x2] = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
