"""Per-pixel features for the canopy classifier (5 cm/px RGB input).

Colour: chromaticity, excess green / red, HSV, Lab.  Context: local means of ExG and brightness
(3 scales), local contrast, brightness relative to the neighbourhood (shadows are darker than the
soil around them, vine leaves are not).
"""
import cv2
import numpy as np

NAMES = ["r", "g", "b", "exg", "exr", "h_sin", "h_cos", "s", "v", "L", "a", "b_lab",
         "exg_m5", "exg_m15", "exg_m41", "v_m5", "v_m15", "v_rel41", "v_std7", "a_m9"]


def features(rgb):
    x = rgb.astype(np.float32)
    s = x.sum(2) + 1.0
    r, g, b = x[..., 0] / s, x[..., 1] / s, x[..., 2] / s
    exg = 2 * g - r - b
    exr = 1.4 * r - g
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV).astype(np.float32)
    h = hsv[..., 0] * (np.pi / 90.0)
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    v = hsv[..., 2] / 255.0
    bl = lambda a, k: cv2.blur(a, (k, k))                                   # noqa: E731
    v_m41 = bl(v, 41)
    vstd = np.sqrt(np.maximum(bl(v * v, 7) - bl(v, 7) ** 2, 0))
    F = [r, g, b, exg, exr, np.sin(h), np.cos(h), hsv[..., 1] / 255.0, v,
         lab[..., 0] / 255.0, (lab[..., 1] - 128) / 128.0, (lab[..., 2] - 128) / 128.0,
         bl(exg, 5), bl(exg, 15), bl(exg, 41), bl(v, 5), bl(v, 15), v - v_m41, vstd,
         bl((lab[..., 1] - 128) / 128.0, 9)]
    return np.stack(F, -1).astype(np.float32)
