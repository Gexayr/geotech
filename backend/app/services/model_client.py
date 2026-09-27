"""Client for the model team's geotech-model service (model-service/).

When MODEL_URL is set and the service answers /health, it owns detection,
measurements and the farmer/auditor routes (see agent-readme.md for the
contract); this backend only converts coordinates and serves the UI. When
it is unset or down, callers fall back to the in-process classical CV and
our own measurements/route code — `available()` is the switch.

Stdlib HTTP only (urllib) — no extra dependency for a handful of calls.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any

MODEL_URL = os.environ.get("MODEL_URL", "").rstrip("/")

HEALTH_TIMEOUT_S = 2
HEALTH_TTL_S = 15
DETECT_TIMEOUT_PER_TILE_S = 30
DETECT_TIMEOUT_MAX_S = 300
MEASUREMENTS_TIMEOUT_S = 30
ROUTE_TIMEOUT_S = 120

_health = {"ok": False, "checked_at": 0.0, "model_version": None}
_health_lock = threading.Lock()


class ModelError(RuntimeError):
    def __init__(self, status: int | None, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _call(method: str, path: str, body: dict | None, timeout: float) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{MODEL_URL}{path}",
        data=data,
        method=method,
        headers={"content-type": "application/json"} if data is not None else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("detail", str(exc))
        except Exception:  # noqa: BLE001 — non-JSON error body
            detail = str(exc)
        raise ModelError(exc.code, detail) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        _mark_down()
        raise ModelError(None, f"model service unreachable: {exc}") from exc


def _mark_down() -> None:
    with _health_lock:
        _health.update(ok=False, checked_at=time.time())


def available() -> bool:
    """True when the service is configured, up and has finished loading —
    cached for HEALTH_TTL_S so hot paths don't ping it on every request."""
    if not MODEL_URL:
        return False
    with _health_lock:
        if time.time() - _health["checked_at"] < HEALTH_TTL_S:
            return _health["ok"]
    try:
        h = _call("GET", "/health", None, HEALTH_TIMEOUT_S)
        ok = bool(h.get("ready"))
        version = h.get("model_version")
    except ModelError:
        ok, version = False, None
    with _health_lock:
        _health.update(ok=ok, checked_at=time.time(), model_version=version)
    return ok


def model_version() -> str | None:
    return _health["model_version"]


def detect(tiles: list[str], force: bool = False) -> dict[str, Any]:
    timeout = min(DETECT_TIMEOUT_MAX_S, DETECT_TIMEOUT_PER_TILE_S * max(1, len(tiles)))
    return _call("POST", "/v1/detect", {"tiles": tiles, "force": force}, timeout)


def delete_tile(tile_name: str) -> None:
    try:
        _call("DELETE", f"/v1/tiles/{urllib.request.quote(tile_name)}", None, HEALTH_TIMEOUT_S * 5)
    except ModelError as exc:
        if exc.status != 404:  # never detected there — nothing to forget
            raise


def measurements(tiles: list[str] | None = None, area_world: list | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if tiles is not None:
        body["tiles"] = tiles
    if area_world is not None:
        body["area_world"] = area_world
    return _call("POST", "/v1/measurements", body, MEASUREMENTS_TIMEOUT_S)


def route(
    role: str,
    tiles: list[str] | None = None,
    area_world: list | None = None,
    start_world: list | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"role": role}
    if tiles is not None:
        body["tiles"] = tiles
    if area_world is not None:
        body["area_world"] = area_world
    if start_world is not None:
        body["start_world"] = start_world
    return _call("POST", "/v1/route", body, ROUTE_TIMEOUT_S)
