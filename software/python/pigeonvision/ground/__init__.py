"""Local recorded/live camera viewer; no flight-control endpoint."""


def run(source: str, *, host: str = "127.0.0.1", port: int = 8768,
        calibration: str | None = None, open_browser: bool = True,
        replay: bool | None = None) -> None:
    """Serve SOURCE (a saved MPEG-TS path or udp:// URL) until interrupted."""
    from .server import run as serve
    serve(source, host=host, port=port, calibration=calibration,
          open_browser=open_browser, replay=replay)


__all__ = ["run"]
