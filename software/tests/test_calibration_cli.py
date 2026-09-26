"""The CLI keeps lens-only diagnostics distinct from rig calibration bundles."""
from pathlib import Path

import pytest

import pigeonvision.calibration as calibration
from pigeonvision.cli import main


@pytest.mark.parametrize("selected,expected,unvalidated", [([], None, False), (["--camera", "B"], ("B",), False),
    (["--camera", "B", "--allow-unvalidated"], ("B",), True)])
def test_intrinsics_cli_selects_camera_without_inventing_a_rig(monkeypatch, capsys, selected, expected, unvalidated):
    calls = []
    def fit(dataset, output, *, camera_ids, allow_unvalidated):
        calls.append((dataset, output, camera_ids, allow_unvalidated))
        return {"validation": {"status": "unvalidated_no_held_out"}}
    monkeypatch.setattr(calibration, "calibrate_intrinsics", fit, raising=False)
    def forbidden(*args, **kwargs):
        raise AssertionError("Intrinsics-only must not use rig calibration")
    monkeypatch.setattr(calibration, "calibrate", forbidden)
    assert main(["calibrate", "--dataset", "dataset.json", "--output", "result", "--intrinsics-only", *selected]) == 0
    assert calls == [(Path("dataset.json"), Path("result"), expected, unvalidated)]
    output = capsys.readouterr().out
    assert "unvalidated_no_held_out" in output
    assert "result/intrinsics.json" in output and "result/calibration.json" not in output


def test_existing_rig_calibration_dispatch_is_preserved(monkeypatch, capsys):
    calls = []
    def fit(dataset, rig, output):
        calls.append((dataset, rig, output))
        return {"validation": {"status": "synthetic_dispatch_fixture"}}
    monkeypatch.setattr(calibration, "calibrate", fit)
    assert main(["calibrate", "--dataset", "dataset.json", "--rig", "rig.json", "--output", "result"]) == 0
    assert calls == [(Path("dataset.json"), Path("rig.json"), Path("result"))]
    assert "result/calibration.json" in capsys.readouterr().out


@pytest.mark.parametrize("mode", [[], ["--rig", "rig.json", "--intrinsics-only"]])
def test_calibration_requires_exactly_one_explicit_mode(mode):
    with pytest.raises(SystemExit) as exc:
        main(["calibrate", "--dataset", "dataset.json", "--output", "result", *mode])
    assert exc.value.code == 2


@pytest.mark.parametrize("option", [["--camera", "A"], ["--allow-unvalidated"]])
def test_intrinsics_options_cannot_silently_change_existing_rig_calibration(monkeypatch, capsys, option):
    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid option combination must fail before fitting")
    monkeypatch.setattr(calibration, "calibrate", forbidden)
    assert main(["calibrate", "--dataset", "dataset.json", "--rig", "rig.json", "--output", "result", *option]) == 2
    assert "only supported with --intrinsics-only" in capsys.readouterr().err
