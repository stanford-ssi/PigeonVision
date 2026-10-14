"""Offline checks of explicit shutdown writes and refusal paths."""
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest

from pigeonvision.radio import rx_guard as guard

LAUNCHER = Path(__file__).resolve().parents[1] / "tools/e200_receive.sh"

class Attr:
    def __init__(self, value="0", stuck=False):
        self._value, self.stuck = value, stuck

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, value):
        if not self.stuck:
            self._value = value


def fixture():
    lo = SimpleNamespace(id="altvoltage1", output=True, attrs={"powerdown": Attr()})
    tx = SimpleNamespace(id="voltage0", output=True, attrs={"hardwaregain": Attr()})
    tone = SimpleNamespace(id="altvoltage0", output=True, attrs={"raw": Attr("1"), "scale": Attr("0.25")})
    phy = SimpleNamespace(channels=[lo, tx], attrs={"calib_mode": Attr("auto"), "calib_mode_available": Attr("auto manual manual_tx_quad")}, debug_attrs={k: Attr("1") for k in ("adi,tx-lo-powerdown-managed-enable", "loopback", "bist_prbs", "bist_tone")})
    phy.find_channel = lambda name, output: next((c for c in phy.channels if c.id == name and c.output == output), None)
    dac = SimpleNamespace(channels=[tone], attrs={})
    ctx = SimpleNamespace(find_device=lambda name: {"ad9361-phy": phy, "cf-ad9361-dds-core-lpc": dac}.get(name))
    return ctx, phy, dac


def test_prepare_disables_hidden_lo_restart_and_dds():
    ctx, phy, dac = fixture()
    assert guard.prepare(ctx) == (1, 1)
    assert phy.channels[0].attrs["powerdown"].value == "1"
    assert phy.debug_attrs["adi,tx-lo-powerdown-managed-enable"].value == "0"
    assert phy.attrs["calib_mode"].value == "manual_tx_quad"
    assert dac.channels[0].attrs["scale"].value == "0"


def test_failed_lo_shutdown_stops_before_other_changes():
    ctx, phy, _ = fixture()
    phy.channels[0].attrs["powerdown"] = Attr("0", stuck=True)
    with pytest.raises(guard.GuardError, match="Readback mismatch"):
        guard.prepare(ctx)
    assert phy.attrs["calib_mode"].value == "auto"


@pytest.mark.parametrize("name", ["adi,tx-lo-powerdown-managed-enable", "loopback", "bist_tone"])
def test_missing_critical_debug_attribute_refuses(name):
    ctx, phy, _ = fixture()
    del phy.debug_attrs[name]
    with pytest.raises(guard.GuardError, match="Missing critical attribute"):
        guard.prepare(ctx)
    assert phy.channels[0].attrs["powerdown"].value == "1"


def test_detectable_active_tx_buffer_refuses():
    ctx, _, dac = fixture()
    dac.attrs["buffer_enabled"] = Attr("1")
    with pytest.raises(guard.GuardError, match="Active TX DMA"):
        guard.prepare(ctx)


def test_missing_dds_scale_refuses():
    ctx, _, dac = fixture()
    del dac.channels[0].attrs["scale"]
    with pytest.raises(guard.GuardError, match="Missing critical attribute: scale"):
        guard.prepare(ctx)


def test_auxiliary_dac_outputs_are_not_rf_tx_channels():
    ctx, phy, _ = fixture()
    phy.channels.append(SimpleNamespace(id="voltage2", output=True, attrs={}))
    phy.channels.append(SimpleNamespace(id="voltage3", output=True, attrs={}))
    assert guard.prepare(ctx) == (1, 1)


def test_managed_restart_failed_readback_refuses():
    ctx, phy, _ = fixture()
    phy.debug_attrs["adi,tx-lo-powerdown-managed-enable"] = Attr("1", stuck=True)
    with pytest.raises(guard.GuardError, match="Readback mismatch"):
        guard.prepare(ctx)
    assert phy.attrs["calib_mode"].value == "auto"


def test_launcher_dry_run_prints_guard_before_receiver_without_access():
    import os
    import subprocess
    launcher = LAUNCHER
    result = subprocess.run(["bash", str(launcher), "915000000", "--dry-run"],
                            env={**os.environ, "E200_PYTHON": sys.executable},
                            capture_output=True, text=True, check=True)
    assert "pigeonvision.radio.rx_guard" in result.stdout
    assert result.stdout.index("pigeonvision.radio.rx_guard") < result.stdout.index("&& dvbs2-rx")
    assert sys.executable in result.stdout




@pytest.mark.parametrize("rate", ["12000000", "16000000"])
def test_launcher_receive_rate_keeps_transmitted_waveform(rate):
    import os
    import shlex
    import subprocess
    launcher = LAUNCHER
    result = subprocess.run(["bash", str(launcher), "915000000", "--dry-run"],
                            env={**os.environ, "E200_PYTHON": sys.executable, "RX_SAMPLE_RATE": rate,
                                 "RX_BUFFER_SAMPLES": "262144"},
                            capture_output=True, text=True, check=True)
    args = shlex.split(result.stdout)
    assert args[args.index("--samp-rate") + 1] == rate
    assert args[args.index("--sym-rate") + 1] == "8000000"
    assert args[args.index("--plutosdr-buf-size") + 1] == "262144"


@pytest.mark.parametrize("settings", [{"RX_SAMPLE_RATE": "14000000"},
                                      {"RX_BUFFER_SAMPLES": "0"}])
def test_launcher_rejects_unsupported_capture_settings(settings):
    import os
    import subprocess
    launcher = LAUNCHER
    result = subprocess.run(["bash", str(launcher), "915000000", "--dry-run"],
                            env={**os.environ, "E200_PYTHON": sys.executable, **settings}, capture_output=True, text=True)
    assert result.returncode == 2
    assert not result.stdout


@pytest.mark.parametrize("actual", ["", "invalid"])
def test_invalid_critical_readback_refuses(actual):
    ctx, phy, _ = fixture()
    phy.channels[0].attrs["powerdown"] = Attr(actual, stuck=True)
    with pytest.raises(guard.GuardError, match="readback"):
        guard.prepare(ctx)
