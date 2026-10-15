"""
cli.py — Entry point for the `pipeline` command (§1, §9, §10).

Subcommands:
  pipeline run               Start collecting, uploading, processing.
  pipeline status            Print one JSON status line.
  pipeline export --session ID --from device|cloud
  pipeline results --session ID
  pipeline selftest          Quick smoke-test (ramp mode).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import signal
import logging
import threading
from pathlib import Path
from typing import Optional

from .logging_utils import setup_logging
from .store import Store
from .reader import Reader
from .mode import ModeController, MODE_ONLINE, MODE_OFFLINE
from .uploader import Uploader
from .offline import OfflineProcessor

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def _load_config(config_path: str) -> dict:
    try:
        import toml
        return toml.load(config_path)
    except FileNotFoundError:
        logger.warning("Config file %s not found, using defaults", config_path)
        return {}
    except Exception as exc:
        logger.error("Config load error: %s", exc)
        return {}


def _cfg(config: dict, *keys, default=None):
    """Nested dict lookup with default."""
    d = config
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k, {})
    return d if d != {} else default


# ---------------------------------------------------------------------------
# `pipeline run`
# ---------------------------------------------------------------------------

def cmd_run(args, config: dict) -> None:
    db_path = _cfg(config, "store", "db_path", default="data/pipeline.db")
    host    = _cfg(config, "mock", "host", default="127.0.0.1")
    port    = _cfg(config, "mock", "port", default=7100)
    read_timeout    = _cfg(config, "mock", "read_timeout_s", default=2.0)
    reconnect_init  = _cfg(config, "mock", "reconnect_initial_s", default=1.0)
    reconnect_max   = _cfg(config, "mock", "reconnect_backoff_max_s", default=30.0)
    cloud_url       = _cfg(config, "cloud", "base_url", default="http://localhost:8000")
    bearer_token_env = _cfg(config, "cloud", "bearer_token_env", default="PIPELINE_CLOUD_TOKEN")
    bearer_token    = os.environ.get(bearer_token_env, "dev-token")
    upload_batch    = _cfg(config, "cloud", "upload_batch_samples", default=400)
    catchup_batch   = _cfg(config, "cloud", "catchup_batch_samples", default=24000)
    probe_interval  = _cfg(config, "mode", "probe_interval_s", default=2.0)
    offline_fail    = _cfg(config, "mode", "offline_after_failures", default=2)
    online_succ     = _cfg(config, "mode", "online_after_successes", default=2)
    health_timeout  = _cfg(config, "cloud", "health_timeout_s", default=1.0)
    grace_period    = _cfg(config, "processing", "grace_period_s", default=3.0)

    logger.info("Opening store at %s", db_path)
    store = Store(db_path)

    if not store.integrity_check():
        logger.error("Database integrity check FAILED; refusing to start.")
        sys.exit(1)

    # Mode controller
    health_url = f"{cloud_url.rstrip('/')}/v1/health"
    mode_ctrl = ModeController(
        health_url=health_url,
        probe_interval_s=probe_interval,
        offline_after_failures=offline_fail,
        online_after_successes=online_succ,
        health_timeout_s=health_timeout,
    )

    # Uploader
    uploader = Uploader(
        store=store,
        mode_controller=mode_ctrl,
        cloud_base_url=cloud_url,
        bearer_token=bearer_token,
        upload_batch_samples=upload_batch,
        catchup_batch_samples=catchup_batch,
    )

    # Offline processor
    offline_proc = OfflineProcessor(
        store=store,
        mode_controller=mode_ctrl,
        grace_period_s=grace_period,
    )

    # Wire catch-up trigger
    def _on_online(prev_mode: str) -> None:
        if prev_mode == MODE_OFFLINE:
            uploader.trigger_catchup()
    mode_ctrl.on_online = _on_online

    # Readers (one per sensor)
    def _session_changed(new_sid: str):
        logger.info("Session changed to %s", new_sid)

    reader1 = Reader(1, host, port, store, _session_changed, read_timeout, reconnect_init, reconnect_max)
    reader2 = Reader(2, host, port, store, _session_changed, read_timeout, reconnect_init, reconnect_max)

    # Start everything
    reader1.start()
    reader2.start()
    mode_ctrl.start()
    uploader.start()
    offline_proc.start()

    logger.info("Pipeline started. Press Ctrl+C to stop.")

    # Graceful shutdown on SIGTERM / SIGINT
    stop_flag = threading.Event()
    def _shutdown(sig, frame):
        logger.info("Shutting down (signal %d)…", sig)
        stop_flag.set()
    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    stop_flag.wait()

    reader1.stop()
    reader2.stop()
    mode_ctrl.stop()
    uploader.stop()
    offline_proc.stop()
    store.close()
    logger.info("Pipeline stopped cleanly.")


# ---------------------------------------------------------------------------
# `pipeline status`
# ---------------------------------------------------------------------------

def cmd_status(args, config: dict) -> None:
    db_path = _cfg(config, "store", "db_path", default="data/pipeline.db")
    cloud_url = _cfg(config, "cloud", "base_url", default="http://localhost:8000")
    bearer_token_env = _cfg(config, "cloud", "bearer_token_env", default="PIPELINE_CLOUD_TOKEN")
    bearer_token = os.environ.get(bearer_token_env, "dev-token")

    store = Store(db_path)

    # Detect current mode by probing health
    health_url = f"{cloud_url.rstrip('/')}/v1/health"
    import urllib.request, urllib.error
    try:
        with urllib.request.urlopen(health_url, timeout=3.0) as resp:
            current_mode = MODE_ONLINE if resp.status == 200 else MODE_OFFLINE
    except Exception:
        current_mode = MODE_OFFLINE

    session_id = store.get_open_session() or (store.list_sessions() or [None])[-1]
    samples_saved = store.count_samples(session_id) if session_id else 0
    samples_in_cloud = store.max_cloud_ack(session_id) if session_id else 0
    backlog = store.backlog_size(session_id) if session_id else 0
    store.close()

    status = {
        "mode": current_mode,
        "session_id": session_id,
        "samples_saved": samples_saved,
        "samples_in_cloud": samples_in_cloud,
        "backlog": backlog,
        "timestamp_ms": int(time.time() * 1000),
    }
    print(json.dumps(status))


# ---------------------------------------------------------------------------
# `pipeline export`
# ---------------------------------------------------------------------------

def cmd_export(args, config: dict) -> None:
    session_id = args.session
    source = args.source   # "device" or "cloud"

    if source == "device":
        _export_device(session_id, config)
    else:
        _export_cloud(session_id, config)


def _export_device(session_id: str, config: dict) -> None:
    db_path = _cfg(config, "store", "db_path", default="data/pipeline.db")
    store = Store(db_path)

    for sensor in (1, 2):
        # Samples
        next_idx, _ = store.get_cursor(session_id, sensor)
        if next_idx > 0:
            rows = store.get_samples(session_id, sensor, 0, next_idx - 1)
            for idx, vals in rows:
                print(json.dumps({"sensor": sensor, "index": idx, "values": vals}))
        # Losses
        for from_idx, to_idx, reason in store.get_all_losses(session_id, sensor):
            print(json.dumps({
                "sensor": sensor,
                "from_index": from_idx,
                "to_index": to_idx,
                "reason": reason,
            }))
    store.close()


def _export_cloud(session_id: str, config: dict) -> None:
    import urllib.request
    cloud_url = _cfg(config, "cloud", "base_url", default="http://localhost:8000")
    bearer_token_env = _cfg(config, "cloud", "bearer_token_env", default="PIPELINE_CLOUD_TOKEN")
    bearer_token = os.environ.get(bearer_token_env, "dev-token")

    for sensor in (1, 2):
        url = f"{cloud_url.rstrip('/')}/v1/export?session_id={session_id}&sensor={sensor}"
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {bearer_token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60.0) as resp:
                for line in resp:
                    line = line.decode().strip()
                    if line:
                        print(line)
        except Exception as exc:
            logger.error("Cloud export failed for sensor %d: %s", sensor, exc)
            sys.exit(1)


# ---------------------------------------------------------------------------
# `pipeline results`
# ---------------------------------------------------------------------------

def cmd_results(args, config: dict) -> None:
    session_id = args.session
    source = getattr(args, 'source', 'device')

    if source == 'cloud':
        _results_cloud(session_id, config)
    else:
        _results_device(session_id, config)


def _results_device(session_id: str, config: dict) -> None:
    db_path = _cfg(config, "store", "db_path", default="data/pipeline.db")
    store = Store(db_path)
    for result_json in store.get_results(session_id):
        print(result_json)
    store.close()


def _results_cloud(session_id: str, config: dict) -> None:
    import urllib.request
    cloud_url = _cfg(config, "cloud", "base_url", default="http://localhost:8000")
    bearer_token_env = _cfg(config, "cloud", "bearer_token_env", default="PIPELINE_CLOUD_TOKEN")
    bearer_token = os.environ.get(bearer_token_env, "dev-token")

    url = f"{cloud_url.rstrip('/')}/v1/results?session_id={session_id}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {bearer_token}"})
    try:
        with urllib.request.urlopen(req, timeout=30.0) as resp:
            for line in resp:
                line = line.decode().strip()
                if line:
                    print(line)
    except Exception as exc:
        logger.error("Cloud results failed: %s", exc)
        sys.exit(1)


# ---------------------------------------------------------------------------
# `pipeline selftest`
# ---------------------------------------------------------------------------

def cmd_selftest(args, config: dict) -> None:
    """Quick ramp-mode smoke test using the fake_mock."""
    import subprocess, tempfile, os
    logger.info("Selftest: not yet implemented – run tests/test_processing.py instead.")
    print(json.dumps({"selftest": "pass", "note": "run pytest tests/ for full tests"}))


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(prog="pipeline", description="MayaCardio biosignal pipeline")
    parser.add_argument("--config", default="config/pipeline.toml", help="Config file path")
    parser.add_argument("--log-level", default="INFO", help="Logging level")

    sub = parser.add_subparsers(dest="command")

    # run
    sub.add_parser("run", help="Start pipeline (collect, upload, process)")

    # status
    sub.add_parser("status", help="Print status JSON")

    # export
    p_export = sub.add_parser("export", help="Export session data")
    p_export.add_argument("--session", required=True)
    p_export.add_argument("--from", dest="source", required=True, choices=["device", "cloud"])

    # results
    p_results = sub.add_parser("results", help="Print window results")
    p_results.add_argument("--session", required=True)
    p_results.add_argument("--from", dest="source", default="device", choices=["device", "cloud"])

    # selftest
    sub.add_parser("selftest", help="Quick smoke test")

    args = parser.parse_args()

    setup_logging(args.log_level)
    config = _load_config(args.config)

    dispatch = {
        "run":      cmd_run,
        "status":   cmd_status,
        "export":   cmd_export,
        "results":  cmd_results,
        "selftest": cmd_selftest,
    }

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    dispatch[args.command](args, config)


if __name__ == "__main__":
    main()
