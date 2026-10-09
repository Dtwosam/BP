from __future__ import annotations

from scripts import report_v4_dedupe_index_health as health


def _index(table: str, *, compact: bool, valid: bool = True) -> dict:
    return {
        "table_name": table,
        "index_name": f"{table}_{'digest_uidx' if compact else 'pkey'}",
        "is_primary": not compact,
        "is_unique": True,
        "is_valid": valid,
        "is_ready": True,
        "bytes": 150_000_000 if compact else 480_000_000,
        "idx_blks_hit": 80,
        "idx_blks_read": 20,
        "idx_scan": 100,
        "idx_tup_read": 10,
        "idx_tup_fetch": 10,
    }


def _tables():
    return [
        {
            "table_name": f"raw_event_dedupe_h{i:02d}",
            "estimated_live_tuples": 1_000_000,
            "estimated_dead_tuples": 10_000,
            "heap_bytes": 100_000_000,
            "all_index_bytes": 250_000_000,
            "last_autovacuum": None,
            "last_autoanalyze": None,
        }
        for i in range(16)
    ]


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def mappings(self):
        return self

    def one(self):
        return self.value


class _Connection:
    def __init__(self, *, read_only: bool = True):
        self.read_only = read_only

    def execute(self, statement):
        sql = str(statement)
        if "SHOW default_transaction_read_only" in sql:
            return _Result("on" if self.read_only else "off")
        assert "pg_size_bytes(current_setting(" in sql
        return _Result({
            "shared_buffers_bytes": 128 * 1024**2,
            "effective_cache_size_bytes": 4 * 1024**3,
        })


def _make_report(monkeypatch, *, compact: bool, bad_index: bool = False):
    tables = _tables()
    rows = [
        _index(
            table["table_name"],
            compact=compact,
            valid=not (bad_index and n == 0),
        )
        for n, table in enumerate(tables)
    ]
    monkeypatch.setattr(health, "_dedupe_tables", lambda _: tables)
    monkeypatch.setattr(health, "_dedupe_indexes", lambda _: rows)
    return health.build_report(_Connection())


def test_compact_digest_indexes_are_the_healthy_current_contract(monkeypatch) -> None:
    report = _make_report(monkeypatch, compact=True)
    assert report["report"] == "v4_dedupe_index_health_v2"
    assert report["contract"]["active_uniqueness_mode"] == "compact_digest_unique"
    assert report["contract"]["active_uniqueness_healthy"] is True
    assert report["contract"]["observed_compact_index_count"] == 16
    assert report["contract"]["all_dedupe_indexes_valid_ready"] is True
    assert report["signals"]["all_primary_indexes_healthy"] is False
    assert report["signals"]["reindex_evidence_threshold_applicable"] is False
    assert report["signals"]["reindex_evidence_threshold_met"] is None
    assert report["totals"]["active_uniqueness_index_bytes"] == 2_400_000_000
    assert report["postgresql_cache"]["active_uniqueness_index_cache_hit_ratio"] == 0.8
    assert all(row["active_uniqueness_index"] for row in report["children"])


def test_legacy_primary_keys_remain_supported(monkeypatch) -> None:
    report = _make_report(monkeypatch, compact=False)
    assert report["contract"]["active_uniqueness_mode"] == "legacy_primary_key"
    assert report["contract"]["active_uniqueness_healthy"] is True
    assert report["signals"]["all_primary_indexes_healthy"] is True
    assert report["signals"]["reindex_evidence_threshold_applicable"] is True
    assert isinstance(report["signals"]["reindex_evidence_threshold_met"], bool)


def test_invalid_compact_uniqueness_fails_closed(monkeypatch) -> None:
    report = _make_report(monkeypatch, compact=True, bad_index=True)
    assert report["contract"]["active_uniqueness_mode"] == "unrecognized_or_invalid"
    assert report["contract"]["active_uniqueness_healthy"] is False
    assert report["contract"]["all_dedupe_indexes_valid_ready"] is False
    assert report["signals"]["reindex_evidence_threshold_met"] is None


def test_report_rejects_writable_connection() -> None:
    import pytest
    with pytest.raises(RuntimeError, match="read-only"):
        health.build_report(_Connection(read_only=False))
