import argparse

from . import run


def main() -> None:
    parser = argparse.ArgumentParser(description="View paired IMX900 MPEG-TS locally")
    parser.add_argument("source", help="Saved .ts file or udp://0.0.0.0:5000")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--calibration")
    parser.add_argument("--record-transport", metavar="PATH", help="Save live received UDP transport to a new .ts file")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    run(args.source, host=args.host, port=args.port, calibration=args.calibration,
        open_browser=not args.no_browser, record_transport=args.record_transport)


if __name__ == "__main__":
    main()
