"""Offline capture CLI checks; no radio or native reader is opened."""
import json
import sys
from types import SimpleNamespace

import pytest

@pytest.fixture
def capture():
    from pigeonvision.radio import capture
    return capture


def radio():
    def attrs(**values):
        return {k: SimpleNamespace(value=str(v)) for k, v in values.items()}
    rx = SimpleNamespace(attrs=attrs(sampling_frequency=12000000, rf_bandwidth=12000000,
                                     gain_control_mode="manual", hardwaregain=0))
    lo = SimpleNamespace(attrs=attrs(powerdown=1, frequency=915000000))
    phy = SimpleNamespace(attrs=attrs(calib_mode="manual_tx_quad 26", ensm_mode="fdd"),
                          debug_attrs={"adi,tx-lo-powerdown-managed-enable": SimpleNamespace(value="0")})
    phy.find_channel = lambda name, output: rx if name == "voltage0" else lo
    adc_channel = SimpleNamespace(attrs=attrs(sampling_frequency=12000000),
                                  data_format=SimpleNamespace(length=16))
    adc = SimpleNamespace(find_channel=lambda name, output: adc_channel)
    ctx = SimpleNamespace(attrs={"fw": "mock"}, set_timeout=lambda value: None,
                          find_device=lambda name: phy if name == "ad9361-phy" else adc)
    return ctx, phy


@pytest.mark.parametrize("failure", [False, True])
def test_rx_measurement_and_failure_report(capture, monkeypatch, tmp_path, failure):
    ctx, _ = radio()
    calls = []
    monkeypatch.setitem(sys.modules, "iio", SimpleNamespace(Context=lambda uri: ctx))
    monkeypatch.setattr(capture, "prepare", lambda context: calls.append("guard"))

    def measure(iio, adc, seconds, buffer_samples, kernel_buffers):
        assert kernel_buffers == 16
        assert seconds == 1
        assert buffer_samples == 262144
        if failure:
            raise OSError("refill timed out")
        return {"capture_completed": True, "delivered_bytes": 64000000,
                "delivered_complex_samples_per_second": 12000000,
                "overflow_observations": 0}

    monkeypatch.setattr(capture, "measure_refills", measure)
    output = tmp_path / "result.json"
    assert capture.main(["--seconds", "1", "--output", str(output)]) == int(failure)
    report = json.loads(output.read_text())
    assert report["capture_completed"] is not failure
    assert report["shutdown_state_valid_after_capture"]
    assert report["contiguous_samples_proven"] is False
    assert calls == ["guard"]


def test_native_refill_counts_bytes_without_copy_and_observes_overflow(capture):
    channels = [SimpleNamespace(id=name, output=False, enabled=True)
                for name in ("voltage0", "voltage1", "voltage2", "voltage3")]
    writes = []
    adc = SimpleNamespace(channels=channels, sample_size=4,
                          set_kernel_buffers_count=lambda count: None,
                          reg_read=lambda register: 4,
                          reg_write=lambda register, value: writes.append((register, value)))
    counts = []
    class Buffer:
        def __init__(self, device, samples, cyclic):
            assert device is adc and not cyclic
            self._buffer = "native-pointer"
        def read(self):
            pytest.fail("Must not copy IQ into Python")
    def refill(pointer):
        assert pointer == "native-pointer"
        counts.append(400)
        return 400
    now = [0.0]
    def clock():
        now[0] += 0.1
        return now[0]
    iio = SimpleNamespace(Buffer=Buffer, _buffer_refill=refill)
    report = capture.measure_refills(iio, adc, 1, 65536, clock=clock)
    assert report["delivered_bytes"] == report["refills"] * 400
    assert report["delivered_complex_samples"] == report["refills"] * 100
    assert len(counts) > report["refills"]  # warmup excluded
    assert report["overflow_observations"] > 0
    assert all(item == (0x80000088, 4) for item in writes)
    assert [c.enabled for c in channels] == [True, True, False, False]


def test_unknown_binding_refuses_before_creating_rx_buffer(capture):
    with pytest.raises(RuntimeError, match="byte-count interface"):
        capture.measure_refills(SimpleNamespace(), None, 1, 65536)


def test_invalid_cli_never_imports_iio(capture, monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "iio", None)
    with pytest.raises(SystemExit) as exc:
        capture.main(["--seconds", "nan", "--output", str(tmp_path / "result.json")])
    assert exc.value.code == 2
