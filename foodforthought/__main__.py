from __future__ import annotations

import argparse
import socket
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn

from . import __version__, launcher
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="foodforthought", description="Local practice management")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--data", type=Path, default=None, help="data folder")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--launch", action="store_true", help="open the app, starting it in the background if needed")
    parser.add_argument("--install-app", action="store_true", help="add FoodForThought to the app launcher")
    parser.add_argument("--remove-app", action="store_true", help="remove FoodForThought from the app launcher")
    args = parser.parse_args(argv)

    cfg = Config(data_dir=args.data or default_data_dir(), port=args.port)
    url = f"http://localhost:{cfg.port}"

    if args.install_app or args.remove_app:
        if not launcher.supported():
            print("Adding FoodForThought to the launcher works on ChromeOS and Linux only for now.")
            sys.exit(1)
        if args.remove_app:
            print("Removed from the launcher." if launcher.remove() else "It was not in the launcher.")
        else:
            launcher.install(cfg.data_dir, cfg.port, DEFAULT_PORT)
            print("FoodForThought is now in the launcher. On a Chromebook it is in the Linux apps folder.")
        return

    if not _port_free(cfg.port):
        if launcher.is_running(cfg.port):
            if args.launch:
                webbrowser.open(url)
                return
            print(f"FoodForThought is already running. Open {url} in Chrome.")
        else:
            print(f"Port {cfg.port} is in use by another program. Try --port 8766.")
        sys.exit(1)

    if args.launch:
        launcher.log_to(cfg.data_dir / "app.log")

    app = create_app(cfg)
    print(f"FoodForThought {__version__}")
    print(f"Data folder: {cfg.data_dir}")
    print(f"Open {url} in Chrome. Press Ctrl+C here to stop.")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=cfg.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
