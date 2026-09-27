"""U-Net for grapevine canopy instance segmentation (5 cm/px RGB).

Output classes: 0 background, 1 plant interior, 2 plant boundary (the outer ~10 cm ring of every
reference plant polygon). Boundary pixels separate touching plants, so instances are recovered by
taking connected interior regions as seeds and growing them over the canopy (interior + boundary)
with a watershed - one polygon per plant, split where the annotators split.
"""
import math
import os

import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

RES = 0.05
MEAN = np.array([0.45, 0.42, 0.35], np.float32)
STD = np.array([0.20, 0.19, 0.17], np.float32)


# ----------------------------------------------------------------------------- model
def _block(ci, co):
    return nn.Sequential(nn.Conv2d(ci, co, 3, padding=1, bias=False), nn.BatchNorm2d(co), nn.ReLU(inplace=True),
                         nn.Conv2d(co, co, 3, padding=1, bias=False), nn.BatchNorm2d(co), nn.ReLU(inplace=True))


class UNet(nn.Module):
    def __init__(self, base=16, n_cls=3):
        super().__init__()
        c = [base, base * 2, base * 4, base * 8, base * 12]
        self.enc = nn.ModuleList([_block(3, c[0])] + [_block(c[i], c[i + 1]) for i in range(4)])
        self.up = nn.ModuleList([nn.ConvTranspose2d(c[i + 1], c[i], 2, stride=2) for i in range(4)])
        self.dec = nn.ModuleList([_block(c[i] * 2, c[i]) for i in range(4)])
        self.head = nn.Conv2d(c[0], n_cls, 1)

    def forward(self, x):
        skips = []
        for i, e in enumerate(self.enc):
            x = e(x if i == 0 else F.max_pool2d(x, 2))
            skips.append(x)
        x = skips.pop()
        for i in reversed(range(4)):
            x = self.up[i](x)
            x = self.dec[i](torch.cat([x, skips[i]], 1))
        return self.head(x)


# ----------------------------------------------------------------------------- labels
def make_target(shape, polys_px):
    """polys_px: list of Nx2 arrays (pixel coords at 5 cm). -> uint8 map 0/1/2 (bg/interior/boundary)."""
    t = np.zeros(shape, np.uint8)
    for p in polys_px:
        m = np.zeros(shape, np.uint8)
        cv2.fillPoly(m, [np.round(p).astype(np.int32)], 1)
        er = cv2.erode(m, np.ones((5, 5), np.uint8))           # ~10 cm boundary ring
        t[(m > 0) & (t == 0)] = 2
        t[er > 0] = 1
    return t


def to_tensor(rgb):
    x = (rgb.astype(np.float32) / 255.0 - MEAN) / STD
    return torch.from_numpy(x.transpose(2, 0, 1).copy())


# ----------------------------------------------------------------------------- training
def augment(img, tgt, rng, size=192):
    H, W = tgt.shape
    s = rng.uniform(0.8, 1.25)
    cs = int(size / s)
    y, x = rng.integers(0, H - cs), rng.integers(0, W - cs)
    im = cv2.resize(img[y:y + cs, x:x + cs], (size, size), interpolation=cv2.INTER_LINEAR)
    tg = cv2.resize(tgt[y:y + cs, x:x + cs], (size, size), interpolation=cv2.INTER_NEAREST)
    k = rng.integers(4)
    im, tg = np.rot90(im, k), np.rot90(tg, k)
    if rng.random() < 0.5:
        im, tg = im[:, ::-1], tg[:, ::-1]
    im = im.astype(np.float32)
    im = im * rng.uniform(0.75, 1.25) + rng.uniform(-20, 20)                      # brightness / contrast
    im = im * rng.uniform(0.9, 1.1, size=3)[None, None]                           # colour balance
    hsv = cv2.cvtColor(np.clip(im, 0, 255).astype(np.uint8), cv2.COLOR_RGB2HSV)
    hsv[..., 0] = (hsv[..., 0].astype(int) + rng.integers(-4, 5)) % 180             # hue shift
    im = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)
    return np.ascontiguousarray(im), np.ascontiguousarray(tg)


def train(samples, steps, ckpt_prefix, log=print, val=None, batch=6, lr=3e-3, ckpt_every=200, seed=0,
          weights=None, init=None, val_fn=None):
    """samples: list of (rgb uint8 HxWx3, target uint8 HxW); weights: sampling probability per sample;
    init: checkpoint to fine-tune from; val_fn(net) -> score used to pick the best checkpoint."""
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    net = UNet()
    if init:
        net.load_state_dict(torch.load(init, map_location="cpu", weights_only=False)["state_dict"])
    pw = None if weights is None else np.asarray(weights, float) / np.sum(weights)
    opt = torch.optim.AdamW(net.parameters(), lr=lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lr, total_steps=steps, pct_start=0.1)
    cls_w = torch.tensor([1.0, 2.0, 3.0])
    hist, best = [], (-1, None)
    for step in range(1, steps + 1):
        xs, ys = [], []
        for _ in range(batch):
            img, tg = samples[rng.choice(len(samples), p=pw) if pw is not None else rng.integers(len(samples))]
            for _try in range(10):                              # prefer crops that contain plants
                a, b = augment(img, tg, rng)
                if (b > 0).mean() > 0.01 or _try == 9:
                    break
            xs.append(to_tensor(a))
            ys.append(torch.from_numpy(b.astype(np.int64)))
        x, y = torch.stack(xs), torch.stack(ys)
        net.train()
        out = net(x)
        ce = F.cross_entropy(out, y, weight=cls_w)
        p = out.softmax(1)[:, 1:].sum(1)                        # canopy probability
        t = (y > 0).float()
        dice = 1 - (2 * (p * t).sum() + 1) / (p.sum() + t.sum() + 1)
        loss = ce + dice
        opt.zero_grad()
        loss.backward()
        opt.step()
        sched.step()
        if step % 50 == 0:
            log(f"   step {step}/{steps}  loss {loss.item():.3f} (ce {ce.item():.3f}, dice {dice.item():.3f})")
        if step % ckpt_every == 0 or step == steps:
            rec = dict(step=step, loss=round(loss.item(), 4))
            if val_fn is not None:
                rec.update(val_fn(net))
            elif val is not None:
                pr = predict(net, val[0])
                m = pr[..., 1:].sum(-1) > 0.5
                g = val[1] > 0
                rec["val_iou"] = round(float((m & g).sum() / max((m | g).sum(), 1)), 4)
            path = f"{ckpt_prefix}_step{step:05d}.pth"
            torch.save(dict(state_dict=net.state_dict(), step=step, meta=rec), path)
            rec["checkpoint"] = path
            hist.append(rec)
            log(f"   checkpoint {path}  {rec}")
            score = rec.get("val_score", rec.get("val_iou", step))
            if score > best[0]:
                best = (score, path)
    return net, hist, best


# ----------------------------------------------------------------------------- inference
class _SMPWrapper(nn.Module):
    """Model trained with kaggle/vine_train.py (segmentation_models_pytorch, ImageNet normalisation)."""

    def __init__(self, ck):
        super().__init__()
        import segmentation_models_pytorch as smp
        cls = {"unet": smp.Unet, "unetpp": smp.UnetPlusPlus, "fpn": smp.FPN}[ck["arch"]]
        self.m = cls(encoder_name=ck["encoder"], encoder_weights=None, in_channels=3, classes=3)
        self.m.load_state_dict(ck["state_dict"])
        self.register_buffer("a", torch.tensor(STD / np.array([0.229, 0.224, 0.225], np.float32)).view(1, 3, 1, 1))
        self.register_buffer("b", torch.tensor((MEAN - np.array([0.485, 0.456, 0.406], np.float32))
                                               / np.array([0.229, 0.224, 0.225], np.float32)).view(1, 3, 1, 1))

    def forward(self, x):                               # our normalisation -> ImageNet normalisation
        return self.m(x * self.a + self.b)


def load(path):
    ck = torch.load(path, map_location="cpu", weights_only=False)
    if ck.get("format") == "smp":
        net = _SMPWrapper(ck)
    else:
        net = UNet()
        net.load_state_dict(ck["state_dict"])
    net.eval()
    return net


@torch.no_grad()
def predict(net, rgb, tile=512, overlap=64):
    """Softmax probabilities (H, W, 3) for an RGB image at 5 cm/px, tiled with overlap."""
    net.eval()
    H, W = rgb.shape[:2]
    out = np.zeros((H, W, 3), np.float32)
    wsum = np.zeros((H, W), np.float32)
    step = tile - overlap
    ys = list(range(0, max(H - tile, 0) + 1, step)) + ([H - tile] if H > tile and (H - tile) % step else [])
    xs = list(range(0, max(W - tile, 0) + 1, step)) + ([W - tile] if W > tile and (W - tile) % step else [])
    w1 = np.hanning(min(tile, H) + 2)[1:-1]
    w2 = np.hanning(min(tile, W) + 2)[1:-1]
    wt = np.outer(w1, w2).astype(np.float32) + 1e-3
    for y in ys or [0]:
        for x in xs or [0]:
            crop = rgb[y:y + tile, x:x + tile]
            h, w = crop.shape[:2]
            ph, pw = (-h) % 32, (-w) % 32
            t = to_tensor(np.pad(crop, ((0, ph), (0, pw), (0, 0)), mode="reflect"))[None]
            p = net(t).softmax(1)[0, :, :h, :w].numpy().transpose(1, 2, 0)
            ww = wt[:h, :w]
            out[y:y + h, x:x + w] += p * ww[..., None]
            wsum[y:y + h, x:x + w] += ww
    return out / np.maximum(wsum, 1e-6)[..., None]


def instances(prob, min_area_m2=0.08, canopy_thr=0.5, interior_thr=0.5):
    """Probabilities -> label image of individual plants (0 = background)."""
    from skimage.segmentation import watershed
    canopy = prob[..., 1] + prob[..., 2] > canopy_thr
    interior = (prob[..., 1] > interior_thr) & canopy
    interior = cv2.morphologyEx(interior.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, seeds = cv2.connectedComponents(interior, connectivity=8)
    # drop tiny seeds (noise) before growing
    areas = np.bincount(seeds.ravel(), minlength=n)
    small = areas < (0.02 / RES ** 2)
    small[0] = False
    seeds[small[seeds]] = 0
    lab = watershed(-prob[..., 1], seeds, mask=canopy)
    areas = np.bincount(lab.ravel())
    lab[(areas < min_area_m2 / RES ** 2)[lab] & (lab > 0)] = 0
    return lab
