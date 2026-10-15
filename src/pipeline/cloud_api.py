"""
cloud_api.py — Cloud-side FastAPI HTTP API (§3.3, §7.2).

Deployed on a free-tier ARM64 VM (e.g., Oracle Cloud Always Free).
Uses the same processing.py module as the device.

Run with:
    uvicorn pipeline.cloud_api:app --host 0.0.0.0 --port 8000

Environment variables:
    PIPELINE_CLOUD_TOKEN   Bearer token (required)
    CLOUD_DB_PATH          SQLite path (default: /data/cloud.db)
    GRACE_PERIOD_S         seconds before emitting incomplete window (default: 3)
"""

from __future__ import annotations

import json
import logging
import os
import time
import threading
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

from .store import Store
from .processing import PeakDetector, compute_window, SAMPLES_PER_WINDOW

logger = logging.getLogger(__name__)

app = FastAPI(title="MayaCardio Cloud API")

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

_BEARER_TOKEN = os.environ.get("PIPELINE_CLOUD_TOKEN", "dev-token")
_security = HTTPBearer()


def _require_auth(creds: HTTPAuthorizationCredentials = Depends(_security)):
    if creds.credentials != _BEARER_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    return creds.credentials


# ---------------------------------------------------------------------------
# Store (cloud-side)
# ---------------------------------------------------------------------------

def _resolve_default_cloud_db() -> str:
    env_path = os.environ.get("CLOUD_DB_PATH")
    if env_path:
        return env_path
    if os.path.isdir("/data") and os.access("/data", os.W_OK):
        return "/data/cloud.db"
    return "data/cloud.db"

_DB_PATH = _resolve_default_cloud_db()
_GRACE_S = float(os.environ.get("GRACE_PERIOD_S", "3"))

_store: Optional[Store] = None
_store_lock = threading.Lock()

def _get_store() -> Store:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = Store(_DB_PATH)
    return _store


# ---------------------------------------------------------------------------
# Cloud-side window processing state (session → PeakDetector)
# ---------------------------------------------------------------------------

_peak_detectors: dict[str, dict[int, PeakDetector]] = {}  # session_id → {window → detector}
_pd_lock = threading.Lock()

def _get_peak_detector(session_id: str) -> PeakDetector:
    """Return (or create) the per-session PeakDetector for cloud processing."""
    with _pd_lock:
        if session_id not in _peak_detectors:
            _peak_detectors[session_id] = PeakDetector()
        return _peak_detectors[session_id]


# ---------------------------------------------------------------------------
# Track highest window processed per session
# ---------------------------------------------------------------------------

_next_cloud_window: dict[str, int] = {}
_ncw_lock = threading.Lock()


def _try_process_windows(session_id: str) -> None:
    """Trigger processing of any ready windows for this session."""
    store = _get_store()
    pd = _get_peak_detector(session_id)

    with _ncw_lock:
        w = _next_cloud_window.get(session_id, 0)

    while True:
        w_end = SAMPLES_PER_WINDOW * (w + 1) - 1
        # Need samples up to w_end + 1 for peak confirmation
        s1 = store.get_samples(session_id, 1, SAMPLES_PER_WINDOW * w, w_end)
        s2 = store.get_samples(session_id, 2, SAMPLES_PER_WINDOW * w, w_end)

        s1_ready = len(s1) == SAMPLES_PER_WINDOW
        s2_ready = len(s2) == SAMPLES_PER_WINDOW

        # Also check losses fill the gaps
        s1_loss = store.get_losses(session_id, 1, SAMPLES_PER_WINDOW * w, w_end)
        s2_loss = store.get_losses(session_id, 2, SAMPLES_PER_WINDOW * w, w_end)

        s1_declared = s1_ready or bool(s1_loss)
        s2_declared = s2_ready or bool(s2_loss)

        # Check if grace period expired
        # We approximate: if data for w_end+1 has been received
        next_s1 = store.get_samples(session_id, 1, w_end + 1, w_end + 1)
        if not (s1_declared and s2_declared) and not next_s1:
            break  # Not ready

        result_time_ms = int(time.time() * 1000)
        try:
            result = compute_window(
                session_id=session_id,
                window=w,
                mode="online",
                store=store,
                peak_detector=pd,
                result_time_ms=result_time_ms,
            )
            store.write_result(
                session_id=session_id,
                window=w,
                mode="online",
                result_json=json.dumps(result),
                result_time_ms=result_time_ms,
            )
            logger.info("Cloud: processed window %d for session %s HR=%s",
                        w, session_id, result.get("heart_rate"))
        except Exception as exc:
            logger.error("Cloud: window %d processing error: %s", w, exc)

        with _ncw_lock:
            w += 1
            _next_cloud_window[session_id] = w


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class SampleItem(BaseModel):
    idx: int
    values: list[int]


class LossItem(BaseModel):
    from_index: int
    to_index: Optional[int] = None
    reason: str


class PostSamplesRequest(BaseModel):
    session_id: str
    sensor: int
    samples: list[SampleItem] = []
    losses: list[LossItem] = []


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/v1/health")
async def health():
    return {"status": "ok", "timestamp_ms": int(time.time() * 1000)}


@app.post("/v1/samples")
async def post_samples(
    body: PostSamplesRequest,
    _: str = Depends(_require_auth),
):
    store = _get_store()
    session_id = body.session_id
    sensor = body.sensor

    store.open_session(session_id)

    # Determine current next_index
    next_idx, _ = store.get_cursor(session_id, sensor)

    samples = [(s.idx, s.values) for s in body.samples]
    losses  = [(l.from_index, l.to_index, l.reason) for l in body.losses]

    # New next_index: max of existing and what we just received
    new_next = next_idx
    if samples:
        new_next = max(new_next, max(s[0] for s in samples) + 1)

    store.write_batch(
        session_id=session_id,
        sensor=sensor,
        samples=samples,
        losses=losses,
        new_next_index=new_next,
    )

    # Trigger window processing in background
    threading.Thread(
        target=_try_process_windows,
        args=(session_id,),
        daemon=True,
    ).start()

    # Return highest contiguous index for this sensor starting from current cloud_ack
    _, current_ack = store.get_cursor(session_id, sensor)
    scan_limit = min(new_next, current_ack + max(len(samples), 400) + 1000)
    recent_samples = store.get_samples(session_id, sensor, current_ack, scan_limit)
    expected = current_ack
    for idx, _ in recent_samples:
        if idx == expected:
            expected += 1
        else:
            break

    return {"session_id": session_id, "sensor": sensor, "highest_contiguous_index": expected}


@app.get("/v1/status")
async def get_status(
    session_id: str,
    _: str = Depends(_require_auth),
):
    store = _get_store()
    result = {}
    for sensor in (1, 2):
        next_idx, _ = store.get_cursor(session_id, sensor)
        result[f"sensor{sensor}_next_index"] = next_idx
    return result


@app.get("/v1/export")
async def export_session(
    session_id: str,
    sensor: int,
    _: str = Depends(_require_auth),
):
    """Stream JSON lines: samples + losses, sorted by index."""
    store = _get_store()
    next_idx, _ = store.get_cursor(session_id, sensor)

    def _generate():
        if next_idx > 0:
            for idx, vals in store.get_samples(session_id, sensor, 0, next_idx - 1):
                yield json.dumps({"sensor": sensor, "index": idx, "values": vals}) + "\n"
        for from_idx, to_idx, reason in store.get_all_losses(session_id, sensor):
            yield json.dumps({
                "sensor": sensor,
                "from_index": from_idx,
                "to_index": to_idx,
                "reason": reason,
            }) + "\n"

    return StreamingResponse(_generate(), media_type="application/x-ndjson")


@app.get("/v1/results")
async def get_results(
    session_id: str,
    _: str = Depends(_require_auth),
):
    """Stream one JSON line per processed window (mode=online)."""
    store = _get_store()
    results = store.get_results(session_id)

    def _generate():
        for r in results:
            yield r + "\n"

    return StreamingResponse(_generate(), media_type="application/x-ndjson")
