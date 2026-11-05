"""Local recorded/live camera viewer; no flight-control endpoint."""


def run(source: str, *, host: str = "127.0.0.1", port: int = 8768,
        calibration: str | None = None, open_browser: bool = True,
        replay: bool | None = None, record_transport: str | None = None,
        radio_monitor_url: str | None = None, pes_access_units: bool = False) -> None:
    """Serve SOURCE (a saved MPEG-TS path or udp:// URL) until interrupted."""
    from .server import run as serve
    serve(source, host=host, port=port, calibration=calibration,
          open_browser=open_browser, replay=replay, record_transport=record_transport,
          radio_monitor_url=radio_monitor_url, pes_access_units=pes_access_units)


__all__ = ["run"]
