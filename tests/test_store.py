"""
test_store.py — Tests for SQLite store: PK dedupe, cursor atomicity, crash safety.
"""

import sys, os, tempfile, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest
from pipeline.store import Store


@pytest.fixture
def tmp_store(tmp_path):
    s = Store(str(tmp_path / "test.db"))
    s.open_session("sess1")
    yield s
    s.close()


class TestPrimaryKeyDedup:
    def test_duplicate_sample_not_stored_twice(self, tmp_store):
        store = tmp_store
        # Write sample idx=0
        store.write_batch("sess1", 1, [(0, [1, 2, 3])], [], 1)
        # Write same sample again — INSERT OR IGNORE should silently drop it
        store.write_batch("sess1", 1, [(0, [999, 999, 999])], [], 1)
        rows = store.get_samples("sess1", 1, 0, 0)
        assert len(rows) == 1
        assert rows[0][1] == [1, 2, 3]  # original values preserved

    def test_different_indexes_both_stored(self, tmp_store):
        store = tmp_store
        store.write_batch("sess1", 1, [(0, [1,2,3]), (1, [4,5,6])], [], 2)
        rows = store.get_samples("sess1", 1, 0, 1)
        assert len(rows) == 2


class TestCursorAtomicity:
    def test_cursor_updated_with_samples(self, tmp_store):
        store = tmp_store
        store.write_batch("sess1", 1, [(0, [1,2,3])], [], 1)
        nxt, _ = store.get_cursor("sess1", 1)
        assert nxt == 1

    def test_cursor_starts_at_zero(self, tmp_store):
        store = tmp_store
        nxt, cloud = store.get_cursor("sess1", 1)
        assert nxt == 0
        assert cloud == 0

    def test_cloud_ack_advances(self, tmp_store):
        store = tmp_store
        store.write_batch("sess1", 1, [(0, [1,2,3])], [], 1)
        store.advance_cloud_ack("sess1", 1, 1)
        _, cloud = store.get_cursor("sess1", 1)
        assert cloud == 1

    def test_cloud_ack_not_backwards(self, tmp_store):
        store = tmp_store
        store.advance_cloud_ack("sess1", 1, 100)
        store.advance_cloud_ack("sess1", 1, 50)  # should not regress
        _, cloud = store.get_cursor("sess1", 1)
        assert cloud == 100


class TestLossRecords:
    def test_loss_stored_and_retrieved(self, tmp_store):
        store = tmp_store
        store.write_batch("sess1", 1, [], [(100, 199, "buffer_overflow")], 200)
        losses = store.get_losses("sess1", 1, 0, 300)
        assert len(losses) == 1
        assert losses[0] == (100, 199, "buffer_overflow")

    def test_loss_pk_dedup(self, tmp_store):
        store = tmp_store
        store.write_batch("sess1", 1, [], [(100, 199, "reason_a")], 200)
        store.write_batch("sess1", 1, [], [(100, 299, "reason_b")], 300)  # duplicate from_index
        losses = store.get_losses("sess1", 1, 0, 400)
        assert len(losses) == 1
        assert losses[0][2] == "reason_a"  # original preserved


class TestIntegrityCheck:
    def test_fresh_db_passes(self, tmp_store):
        assert tmp_store.integrity_check() is True


class TestMultiSession:
    def test_sessions_isolated(self, tmp_path):
        store = Store(str(tmp_path / "ms.db"))
        store.open_session("A")
        store.open_session("B")
        store.write_batch("A", 1, [(0, [1,2,3])], [], 1)
        store.write_batch("B", 1, [(0, [9,9,9])], [], 1)
        a_rows = store.get_samples("A", 1, 0, 0)
        b_rows = store.get_samples("B", 1, 0, 0)
        assert a_rows[0][1] == [1,2,3]
        assert b_rows[0][1] == [9,9,9]
        store.close()


class TestConcurrentWrites:
    def test_concurrent_sensors(self, tmp_store):
        store = tmp_store
        errors = []
        def write_sensor(sensor_id):
            try:
                for i in range(0, 100):
                    store.write_batch(
                        "sess1", sensor_id,
                        [(i, [i, i+1, i+2] if sensor_id == 1 else [i, i+1])],
                        [], i + 1
                    )
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=write_sensor, args=(1,))
        t2 = threading.Thread(target=write_sensor, args=(2,))
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert not errors
        r1 = store.get_samples("sess1", 1, 0, 99)
        r2 = store.get_samples("sess1", 2, 0, 99)
        assert len(r1) == 100
        assert len(r2) == 100
