import json

from monitor import advice


class TestReadAdvice:
    def test_missing_file_returns_unavailable(self, tmp_path):
        result = advice.read_advice(tmp_path / "nope.json")
        assert result["available"] is False
        assert result["targets"] == []

    def test_malformed_json_returns_unavailable_with_error(self, tmp_path):
        path = tmp_path / "advice.json"
        path.write_text("{not json at all", encoding="utf-8")
        result = advice.read_advice(path)
        assert result["available"] is False
        assert result["error"] is not None

    def test_valid_file_is_parsed(self, tmp_path):
        path = tmp_path / "advice.json"
        path.write_text(
            json.dumps(
                {
                    "written_at": "2026-11-06T14:22:00",
                    "summary": "RAM at 78 percent.",
                    "targets": [
                        {
                            "pid": 4411,
                            "create_time": 1762406400.0,
                            "name": "Teams.exe",
                            "reason": "idle 42m",
                            "est_mb": 900,
                        }
                    ],
                    "notes": "Code.exe left alone.",
                }
            ),
            encoding="utf-8",
        )
        result = advice.read_advice(path)
        assert result["available"] is True
        assert result["summary"] == "RAM at 78 percent."
        assert result["targets"][0]["pid"] == 4411
        assert result["notes"] == "Code.exe left alone."

    def test_targets_without_pid_are_dropped(self, tmp_path):
        path = tmp_path / "advice.json"
        path.write_text(
            json.dumps({"targets": [{"name": "x.exe"}, {"pid": 5, "create_time": 1.0}]}),
            encoding="utf-8",
        )
        result = advice.read_advice(path)
        assert [t["pid"] for t in result["targets"]] == [5]

    def test_non_object_json_returns_unavailable(self, tmp_path):
        path = tmp_path / "advice.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        result = advice.read_advice(path)
        assert result["available"] is False

    def test_missing_keys_get_safe_defaults(self, tmp_path):
        path = tmp_path / "advice.json"
        path.write_text("{}", encoding="utf-8")
        result = advice.read_advice(path)
        assert result["available"] is True
        assert result["targets"] == []
        assert result["summary"] == ""


class TestUrgency:
    def test_urgency_is_read_and_lowercased(self, tmp_path):
        path = tmp_path / "advice.json"
        path.write_text(json.dumps({"urgency": "HIGH"}), encoding="utf-8")
        assert advice.read_advice(path)["urgency"] == "high"

    def test_absent_urgency_defaults_to_empty(self, tmp_path):
        path = tmp_path / "advice.json"
        path.write_text("{}", encoding="utf-8")
        assert advice.read_advice(path)["urgency"] == ""

    def test_missing_file_has_an_urgency_key(self, tmp_path):
        assert advice.read_advice(tmp_path / "nope.json")["urgency"] == ""
