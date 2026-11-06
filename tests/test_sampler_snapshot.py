import json

from monitor.sampler import Sampler


class TestSnapshotCarriesPerProcessRows:
    """Claude runs with Read and Write only.

    Without per-process rows in the snapshot it cannot learn any
    create_time, and the advice contract requires one per target. The
    first headless run copied a stale create_time from the previous
    advice file, which the PID reuse guard rejects as soon as the process
    has restarted.
    """

    def test_snapshot_includes_a_processes_list(self, tmp_path, monkeypatch):
        from monitor import constants, sampler as sampler_mod

        path = tmp_path / "snapshot.json"
        monkeypatch.setattr(sampler_mod, "SNAPSHOT_PATH", path)
        monkeypatch.setattr(sampler_mod, "DATA_DIR", tmp_path)

        Sampler().sample()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "processes" in data
        assert data["processes"], "processes list must not be empty"

    def test_every_row_carries_pid_and_create_time(self, tmp_path, monkeypatch):
        from monitor import sampler as sampler_mod

        path = tmp_path / "snapshot.json"
        monkeypatch.setattr(sampler_mod, "SNAPSHOT_PATH", path)
        monkeypatch.setattr(sampler_mod, "DATA_DIR", tmp_path)

        Sampler().sample()
        rows = json.loads(path.read_text(encoding="utf-8"))["processes"]
        for row in rows:
            assert isinstance(row["pid"], int)
            assert "create_time" in row
            assert "name" in row
            assert "rss" in row

    def test_rows_are_capped_and_sorted_by_memory(self, tmp_path, monkeypatch):
        from monitor import constants, sampler as sampler_mod

        path = tmp_path / "snapshot.json"
        monkeypatch.setattr(sampler_mod, "SNAPSHOT_PATH", path)
        monkeypatch.setattr(sampler_mod, "DATA_DIR", tmp_path)

        Sampler().sample()
        rows = json.loads(path.read_text(encoding="utf-8"))["processes"]
        assert len(rows) <= constants.SNAPSHOT_PROCESS_ROWS
        sizes = [r["rss"] for r in rows]
        assert sizes == sorted(sizes, reverse=True)

    def test_rows_with_no_create_time_are_excluded(self, tmp_path, monkeypatch):
        # an access-denied process has create_time None and cannot be a target
        from monitor import sampler as sampler_mod

        path = tmp_path / "snapshot.json"
        monkeypatch.setattr(sampler_mod, "SNAPSHOT_PATH", path)
        monkeypatch.setattr(sampler_mod, "DATA_DIR", tmp_path)

        Sampler().sample()
        rows = json.loads(path.read_text(encoding="utf-8"))["processes"]
        assert all(r["create_time"] is not None for r in rows)
