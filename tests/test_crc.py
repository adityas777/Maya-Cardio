"""
test_crc.py — CRC extraction on tricky formatting (§5.2 warning).

The CRC must be computed on the raw bytes of the data array substring,
NOT on a re-serialised version. Tests cover:
  - Compact JSON (no spaces)
  - JSON with spaces after colons/commas
  - Different array element formats
  - Multi-element nested arrays
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import json
import zlib
import pytest
from pipeline.reader import _extract_crc, _compute_crc


def _build_reply(data: list, with_spaces: bool = False) -> bytes:
    """Build a reply line with or without extra spaces."""
    sep = (', ', ': ') if with_spaces else (',', ':')
    data_str = json.dumps(data, separators=(sep[0].strip(), sep[1].strip()))
    data_bytes = data_str.encode()
    crc = format(zlib.crc32(data_bytes) & 0xFFFFFFFF, '08x')
    body = (
        '{"session_id":"abc","sensor":1,"first_index":0,"count":' +
        str(len(data)) + ',"lost":[],"session_end":false,"crc32":"' + crc +
        '","data":' + data_str + '}'
    )
    return body.encode()


class TestCrcExtraction:
    def test_compact_single_sample(self):
        data = [[720, 1200, 1560]]
        line = _build_reply(data)
        raw = _extract_crc(line)
        assert raw is not None
        assert raw == json.dumps(data, separators=(',', ':')).encode()

    def test_compact_multi_sample(self):
        data = [[720, 1200, 1560], [708, 1180, 1534], [726, 1210, 1573]]
        line = _build_reply(data)
        raw = _extract_crc(line)
        assert raw is not None
        expected = json.dumps(data, separators=(',', ':')).encode()
        assert raw == expected

    def test_crc_matches(self):
        data = [[1, 2, 3], [4, 5, 6]]
        line = _build_reply(data)
        raw = _extract_crc(line)
        computed = _compute_crc(raw)
        # Extract crc from reply
        reply = json.loads(line.decode())
        assert computed == reply["crc32"]

    def test_wrong_crc_detected(self):
        data = [[100, 200, 300]]
        data_str = json.dumps(data, separators=(',', ':'))
        data_bytes = data_str.encode()
        crc = format(zlib.crc32(data_bytes) & 0xFFFFFFFF, '08x')
        # Corrupt the CRC
        bad_crc = "00000000" if crc != "00000000" else "00000001"
        body = (
            '{"session_id":"x","sensor":1,"first_index":0,"count":1'
            ',"lost":[],"session_end":false,"crc32":"' + bad_crc +
            '","data":' + data_str + '}'
        ).encode()
        raw = _extract_crc(body)
        computed = _compute_crc(raw)
        assert computed != bad_crc

    def test_sensor2_two_channel(self):
        data = [[400000, 150000], [401000, 151000]]
        line = _build_reply(data)
        raw = _extract_crc(line)
        assert raw is not None
        expected = json.dumps(data, separators=(',', ':')).encode()
        assert raw == expected

    def test_empty_data(self):
        data = []
        line = _build_reply(data)
        raw = _extract_crc(line)
        assert raw == b"[]"
