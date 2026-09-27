"""Fetches the low-res satellite background shown under the real tiles.

One static JPEG instead of a live tile layer: the map runs on a local
EPSG:32635 metre plane (CRS.Simple), which web-mercator tile services can't
drop into, but ArcGIS's `export` endpoint renders any bbox directly in
EPSG:32635 — so the image lines up with no reprojection on our side.

Covers the study area plus MARGIN_M on every side (enough for the 500 m
max zoom-out), at ~2.5 m/px, re-encoded to a ~1 MB JPEG. Source: Esri
World Imagery — attribution "Imagery © Esri" is shown on the map. Needs
Pillow (in requirements.txt); run from backend/:

    .venv/bin/python scripts/fetch_basemap.py ../frontend/public/basemap.jpg

Prints the local-plane bounds to use for BASEMAP_BOUNDS in SiteMap.tsx.
"""

from __future__ import annotations

import io
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.data import real_site  # noqa: E402

MARGIN_M = 3000
RESOLUTION_M = 2.5
JPEG_QUALITY = 60
EXPORT_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export"


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("basemap.jpg")
    extent_x, extent_y = real_site.get_extent_local()
    x0, y0 = -MARGIN_M, -MARGIN_M
    x1, y1 = extent_x + MARGIN_M, extent_y + MARGIN_M
    wx0, wy0 = real_site.to_world(x0, y0)
    wx1, wy1 = real_site.to_world(x1, y1)
    width = round((x1 - x0) / RESOLUTION_M)
    height = round((y1 - y0) / RESOLUTION_M)

    params = {
        "bbox": f"{wx0},{wy0},{wx1},{wy1}",
        "bboxSR": 32635,
        "imageSR": 32635,
        "size": f"{width},{height}",
        "format": "jpg",
        "f": "image",
    }
    url = f"{EXPORT_URL}?{urllib.parse.urlencode(params, safe=',')}"
    # Esri renders this on demand (20-50 s) and its gateway sometimes gives
    # up with a 504 — just ask again.
    for attempt in range(1, 5):
        try:
            with urllib.request.urlopen(url, timeout=180) as resp:
                if resp.headers.get_content_type() != "image/jpeg":
                    raise SystemExit(f"Unexpected response: {resp.headers.get_content_type()}")
                data = resp.read()
            break
        except urllib.error.HTTPError as exc:
            if attempt == 4 or exc.code not in (502, 503, 504):
                raise
            print(f"Attempt {attempt}: HTTP {exc.code}, retrying...", file=sys.stderr)

    # Esri ignores compressionQuality for this service (~3 MB back) — this
    # is only a background, so re-encode it much smaller.
    buf = io.BytesIO()
    Image.open(io.BytesIO(data)).convert("RGB").save(
        buf, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True
    )
    data = buf.getvalue()

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"Wrote {out} ({len(data) / 1e6:.2f} MB, {width}x{height}px)")
    print(f"BASEMAP_BOUNDS (local x0, y0, x1, y1) = [{x0}, {y0}, {x1:.1f}, {y1:.1f}]")


if __name__ == "__main__":
    main()
