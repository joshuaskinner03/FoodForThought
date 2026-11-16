from __future__ import annotations

import argparse
import socket
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn

from . import __version__
from .app import create_app
from .config import DEFAULT_PORT, Config, default_data_dir


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(prog="larder", description="Local practice management")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--data", type=Path, default=None, help="data folder")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    cfg = Config(data_dir=args.data or default_data_dir(), port=args.port)
    url = f"http://localhost:{cfg.port}"
    if not _port_free(cfg.port):
        print(f"Larder already seems to be running. Open {url} in Chrome.")
        sys.exit(1)

    app = create_app(cfg)
    print(f"Larder {__version__}")
    print(f"Data folder: {cfg.data_dir}")
    print(f"Open {url} in Chrome. Press Ctrl+C here to stop.")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=cfg.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
