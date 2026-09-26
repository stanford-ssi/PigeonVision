"""Synthetic summary schema checks; these fixtures are not hardware evidence."""
import importlib.util
import json
from pathlib import Path

import pytest

TOOL = Path(__file__).parents[1] / "tools/plot_encoder_optimization.py"
spec = importlib.util.spec_from_file_location("plot_encoder_optimization", TOOL)
plot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plot)


@pytest.fixture
def sources(tmp_path):
    for filename, campaign, expected in plot.EXPECTED:
        rows = []
        for i, (name, _, kind) in enumerate(expected):
            config = dict(plot.PROFILE, udp_destination="synthetic:1234", cameras=[{"id": "A"}, {"id": "B"}],
                          encoder_input="copy" if kind == "copy" else "dmabuf")
            if campaign == "allocator":
                config["capture_allocator"] = "dma_heap_cached" if kind == "cached" else "libcamera"
            timing = 20_000 if kind == "cached" else 40_000
            rows.append({"name": name, "configuration": config, "capture_exit": 0, "archive_copy_exit": 0,
                         "measured_seconds": 79.5, "cpu_mean_percent": 60 + i, "temperature_max_c": 50,
                         "throttled_values": [0], "transport_additional_drops": 0,
                         "cameras": {c: {"captured_frames": 30, "encoded_frames": 25+i,
                                         "additional_drops": 0 if kind == "cached" else 5,
                                         "timing_mean_us": {"encoder_input_and_send": timing,
                                                            "capture_queue_dwell": 1_000}}
                                     for c in "AB"}})
        (tmp_path / filename).write_text(json.dumps(rows))
    return tmp_path


def alter(sources, change):
    path = sources / "allocator-comparison.json"
    rows = json.loads(path.read_text())
    change(rows)
    path.write_text(json.dumps(rows))


def test_units_counts_and_rates_are_preserved_without_second_division(sources):
    summary = plot.load_sources(sources)
    assert len(summary["trials"]) == 6
    cached = summary["trials"][4]
    assert cached["cameras"]["A"]["encoded_fps"] == 26
    assert cached["cameras"]["A"]["input_and_send_ms"] == 20
    assert cached["cameras"]["B"]["queue_dwell_ms"] == 1
    assert cached["cameras"]["A"]["added_frame_drops"] == 0
    assert summary["descriptive_comparison"]["input_and_send_reduction_percent"] == 50
    assert summary["qualification_status"] == "short_trials_not_one_hour_qualification"
    assert all(len(source["sha256"]) == 64 for source in summary["sources"])


@pytest.mark.parametrize("mutation,reason", [
    (lambda rows: rows.pop(), "all three"),
    (lambda rows: rows.reverse(), "all three"),
    (lambda rows: rows[0]["configuration"].update(width=1552), "configuration differs"),
    (lambda rows: rows[0]["configuration"].update(encoder_threads=3), "configuration differs"),
    (lambda rows: rows[1]["configuration"].update(capture_allocator="libcamera"), "allocator"),
    (lambda rows: rows[1].update(archive_copy_exit=1), "did not finish"),
    (lambda rows: rows[1].update(cpu_mean_percent=300), "0..100"),
    (lambda rows: rows[1]["cameras"]["A"]["timing_mean_us"].update(encoder_input_and_send=None), "finite"),
    (lambda rows: rows[1]["cameras"]["A"].update(additional_drops=None), "integer"),
    (lambda rows: rows[1]["cameras"]["A"].update(encoded_frames=float("nan")), "finite"),
])
def test_partial_or_mislabelled_evidence_is_not_rendered(sources, mutation, reason):
    alter(sources, mutation)
    with pytest.raises(ValueError, match=reason):
        plot.load_sources(sources)


def test_export_refuses_to_overwrite_before_importing_plotting_library(sources, tmp_path):
    output = tmp_path / "existing-export"
    output.mkdir()
    sentinel = output / "source-summary.json"
    sentinel.write_text("keep this previous evidence")
    with pytest.raises(FileExistsError):
        plot.export(sources, output)
    assert sentinel.read_text() == "keep this previous evidence"


def test_readme_preserves_live_scene_and_startup_limits(sources):
    text = plot.evidence_readme(plot.load_sources(sources))
    assert "not replay of identical raw frames" in text
    assert "not one-hour qualification" in text
    assert "does not imply zero startup drops" in text
    assert "not browser-delivered or displayed fps" in text
