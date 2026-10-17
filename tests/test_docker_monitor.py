from datetime import datetime, timezone

from app.infrastructure.docker_monitor import inspect_container


class FakeContainer:
    id = "abcdef1234567890"
    short_id = "abcdef123456"
    name = "oran-energy-lab-test"
    status = "running"

    def __init__(self):
        self.attrs = {
            "Config": {"Image": "wcnc_tap"},
            "RestartCount": 2,
            "State": {
                "Status": "running",
                "StartedAt": "2026-09-07T10:00:00Z",
                "FinishedAt": "0001-01-01T00:00:00Z",
                "ExitCode": 0,
                "Health": {"Status": "healthy"},
            },
        }

    def reload(self):
        return None

    def stats(self, stream=False):
        assert stream is False
        return {
            "cpu_stats": {
                "cpu_usage": {"total_usage": 300, "percpu_usage": [1, 2]},
                "system_cpu_usage": 1200,
                "online_cpus": 2,
            },
            "precpu_stats": {
                "cpu_usage": {"total_usage": 200},
                "system_cpu_usage": 1000,
            },
            "memory_stats": {"usage": 52428800, "limit": 104857600},
        }


def test_container_snapshot_includes_only_actual_inspection_data():
    snapshot = inspect_container(
        FakeContainer(), now=datetime(2026, 9, 7, 10, 2, 0, tzinfo=timezone.utc)
    )

    assert snapshot["state"] == "running"
    assert snapshot["health"] == "healthy"
    assert snapshot["restart_count"] == 2
    assert snapshot["uptime_s"] == 120.0
    assert snapshot["cpu_pct"] == 100.0
    assert snapshot["memory_pct"] == 50.0
