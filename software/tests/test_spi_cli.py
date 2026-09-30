"""SPI CLI dispatch and portable operation without Linux hardware bindings."""
from pathlib import Path
import sys

import pytest

from pigeonvision.cli import main
import pigeonvision.spi_transport as spi_transport


def test_base_and_spi_help_do_not_require_linux_bindings(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "spidev", None)
    monkeypatch.setitem(sys.modules, "gpiod", None)
    for args in (["--help"], ["spi", "--help"]):
        with pytest.raises(SystemExit) as exc:
            main(args)
        assert exc.value.code == 0
    assert "--mirror-udp" in capsys.readouterr().out


def test_spec_vector_runs_through_pv_without_hardware(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "spidev", None)
    monkeypatch.setitem(sys.modules, "gpiod", None)
    assert main(["spi", "--vector"]) == 0
    printed = capsys.readouterr()
    frame = bytes.fromhex(printed.out)
    assert len(frame) == 1332
    assert frame[:16] == bytes.fromhex("50 56 01 01 01 00 bc 00 00 00 00 00 47 01 00 10")
    assert frame[16:200] == bytes(range(184))
    assert frame[200:1328] == bytes(1128)
    assert frame[-4:] == bytes.fromhex("8d 32 be 51")
    assert "0x51be328d" in printed.err


def test_spi_dispatch_preserves_transport_options_and_exit_code(monkeypatch):
    observed = []

    def send(args):
        observed.append(args)
        return 2

    monkeypatch.setattr(spi_transport, "run_from_args", send)
    assert main(["spi", "--udp", "127.0.0.1:1234", "--hz", "20e6",
                 "--mirror-udp", "192.0.2.1:1234", "--summary", "output/spi/run.json"]) == 2
    args = observed[0]
    assert args.udp == "127.0.0.1:1234"
    assert args.hz == 20000000
    assert args.mirror_udp == "192.0.2.1:1234"
    assert args.summary == Path("output/spi/run.json")
