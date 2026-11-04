import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import usage  # noqa: E402


def _with_ledger(tmp):
    return mock.patch.object(usage, "_ledger_path", return_value=Path(tmp) / "usage_ledger.json")


def _task(cost_in=1000):
    t = usage.TaskUsage(model="claude-sonnet-4-5-20250929")
    t.add(input_tokens=cost_in, output_tokens=10)
    return t


def test_record_roundtrip_and_no_temp_left():
    with tempfile.TemporaryDirectory() as d, _with_ledger(d):
        usage.record(_task())
        usage.record(_task())
        data = json.loads((Path(d) / "usage_ledger.json").read_text())
        assert data["lifetime"]["tasks"] == 2
        assert data["lifetime"]["input"] == 2000
        assert [p.name for p in Path(d).iterdir()] == ["usage_ledger.json"]


def test_corrupt_ledger_is_preserved_not_silently_overwritten():
    with tempfile.TemporaryDirectory() as d, _with_ledger(d):
        (Path(d) / "usage_ledger.json").write_text("{truncated", encoding="utf-8")
        usage.record(_task())
        names = sorted(p.name for p in Path(d).iterdir())
        assert "usage_ledger.json.corrupt" in names
        assert (Path(d) / "usage_ledger.json.corrupt").read_text() == "{truncated"
        assert json.loads((Path(d) / "usage_ledger.json").read_text())["lifetime"]["tasks"] == 1


def test_partial_ledger_missing_keys_does_not_crash():
    with tempfile.TemporaryDirectory() as d, _with_ledger(d):
        (Path(d) / "usage_ledger.json").write_text(json.dumps({"by_day": {}}), encoding="utf-8")
        usage.record(_task())
        assert "Lifetime" in usage.rollup_summary()


def test_zero_call_task_not_recorded():
    with tempfile.TemporaryDirectory() as d, _with_ledger(d):
        usage.record(usage.TaskUsage())
        assert not (Path(d) / "usage_ledger.json").exists()


def test_cost_unknown_model_uses_fallback_price():
    t = usage.TaskUsage(model="mystery")
    t.add(input_tokens=1_000_000)
    assert abs(t.cost_usd() - 3.0) < 1e-9


def test_partial_day_row_does_not_keyerror_on_record_or_rollup():
    from datetime import date
    today = date.today().isoformat()
    with tempfile.TemporaryDirectory() as d, _with_ledger(d):
        (Path(d) / "usage_ledger.json").write_text(
            json.dumps({"by_day": {today: {"tasks": 1}, "2020-01-01": "junk"},
                        "lifetime": {"tasks": 1, "cost_usd": "x"}}), encoding="utf-8")
        usage.record(_task())
        data = json.loads((Path(d) / "usage_ledger.json").read_text())
        assert data["by_day"][today]["tasks"] == 2
        assert data["by_day"][today]["input"] == 1000
        assert data["lifetime"]["tasks"] == 2
        assert "Today:" in usage.rollup_summary()


def test_concurrent_records_do_not_lose_updates():
    import threading
    with tempfile.TemporaryDirectory() as d, _with_ledger(d):
        ts = [threading.Thread(target=lambda: usage.record(_task())) for _ in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert json.loads((Path(d) / "usage_ledger.json").read_text())["lifetime"]["tasks"] == 8
