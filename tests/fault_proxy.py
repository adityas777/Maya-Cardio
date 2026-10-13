"""
fault_proxy.py — TCP proxy that injects faults (§5.3).

Sits between the pipeline reader and the mock. Can:
  - Corrupt replies (change a byte → bad CRC)
  - Duplicate replies (send same reply twice)
  - Drop replies (timeout simulation)
  - Delay replies (latency injection)
  - Drop one sensor's data (sensor silent test)

Usage:
    python tests/fault_proxy.py --listen-port 7200 --upstream-port 7100 \\
        --corrupt-rate 0.1 --duplicate-rate 0.1 --drop-sensor 2
"""

from __future__ import annotations

import argparse
import random
import socket
import threading
import time
import logging

logger = logging.getLogger(__name__)


class FaultProxy:
    def __init__(
        self,
        listen_host: str,
        listen_port: int,
        upstream_host: str,
        upstream_port: int,
        corrupt_rate:   float = 0.0,
        duplicate_rate: float = 0.0,
        drop_rate:      float = 0.0,
        delay_s:        float = 0.0,
        drop_sensor:    int   = 0,     # 0 = no sensor dropped
        seed:           int   = 42,
    ) -> None:
        self.listen_host  = listen_host
        self.listen_port  = listen_port
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self.corrupt_rate  = corrupt_rate
        self.duplicate_rate = duplicate_rate
        self.drop_rate     = drop_rate
        self.delay_s       = delay_s
        self.drop_sensor   = drop_sensor
        self._rng = random.Random(seed)

    def serve_forever(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((self.listen_host, self.listen_port))
            srv.listen(5)
            logger.info(
                "FaultProxy %s:%d → %s:%d",
                self.listen_host, self.listen_port,
                self.upstream_host, self.upstream_port,
            )
            while True:
                conn, addr = srv.accept()
                logger.info("FaultProxy: client %s", addr)
                threading.Thread(
                    target=self._handle,
                    args=(conn,),
                    daemon=True,
                ).start()

    def _handle(self, client: socket.socket) -> None:
        try:
            upstream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            upstream.connect((self.upstream_host, self.upstream_port))
            upstream.settimeout(5.0)
            client.settimeout(5.0)

            up_buf = b""
            cl_buf = b""

            while True:
                # Relay client → upstream
                try:
                    data = client.recv(4096)
                    if not data:
                        break
                    cl_buf += data
                    while b"\n" in cl_buf:
                        line, cl_buf = cl_buf.split(b"\n", 1)
                        upstream.sendall(line + b"\n")
                except socket.timeout:
                    pass

                # Relay upstream → client (with fault injection)
                try:
                    data = upstream.recv(65536)
                    if not data:
                        break
                    up_buf += data
                    while b"\n" in up_buf:
                        line, up_buf = up_buf.split(b"\n", 1)
                        self._relay(client, line + b"\n")
                except socket.timeout:
                    pass
        except Exception as exc:
            logger.debug("FaultProxy: %s", exc)
        finally:
            client.close()

    def _relay(self, client: socket.socket, line: bytes) -> None:
        import json
        try:
            obj = json.loads(line.decode().strip())
            sensor = obj.get("sensor", 0)
        except Exception:
            client.sendall(line)
            return

        # Drop sensor
        if self.drop_sensor and sensor == self.drop_sensor:
            logger.debug("FaultProxy: dropping sensor %d reply", sensor)
            return

        # Drop
        if self._rng.random() < self.drop_rate:
            logger.debug("FaultProxy: dropping reply")
            return

        # Delay
        if self.delay_s > 0:
            time.sleep(self.delay_s)

        # Corrupt (flip one byte in the data)
        if self._rng.random() < self.corrupt_rate:
            logger.debug("FaultProxy: corrupting reply")
            line_list = bytearray(line)
            pos = self._rng.randint(max(0, len(line_list) - 50), len(line_list) - 2)
            line_list[pos] ^= 0x01
            line = bytes(line_list)

        client.sendall(line)

        # Duplicate
        if self._rng.random() < self.duplicate_rate:
            logger.debug("FaultProxy: duplicating reply")
            client.sendall(line)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="TCP fault injection proxy")
    parser.add_argument("--listen-port", type=int, default=7200)
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--upstream-port", type=int, default=7100)
    parser.add_argument("--upstream-host", default="127.0.0.1")
    parser.add_argument("--corrupt-rate",   type=float, default=0.0)
    parser.add_argument("--duplicate-rate", type=float, default=0.0)
    parser.add_argument("--drop-rate",      type=float, default=0.0)
    parser.add_argument("--delay-s",        type=float, default=0.0)
    parser.add_argument("--drop-sensor",    type=int,   default=0)
    parser.add_argument("--seed",           type=int,   default=42)
    args = parser.parse_args()

    proxy = FaultProxy(
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        upstream_host=args.upstream_host,
        upstream_port=args.upstream_port,
        corrupt_rate=args.corrupt_rate,
        duplicate_rate=args.duplicate_rate,
        drop_rate=args.drop_rate,
        delay_s=args.delay_s,
        drop_sensor=args.drop_sensor,
        seed=args.seed,
    )
    proxy.serve_forever()
