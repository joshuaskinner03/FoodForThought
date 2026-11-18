from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from .web import PKG

APP_ID = "foodforthought"
ICON_SIZES = (48, 64, 128, 256, 512)
PROGRAM_DIR = PKG.parent
DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Version=1.0
Name=FoodForThought
Comment=Client records and nutrition analysis
Exec={exec}
Icon={icon}
Terminal=false
Categories=Office;
StartupNotify=false
"""


def supported() -> bool:
    return sys.platform.startswith("linux")


def is_running(port: int) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/status", timeout=2) as response:
            return "initialised" in json.load(response)
    except (OSError, ValueError):
        return False


def log_to(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    log = open(path, "w", encoding="utf-8")
    for stream in (sys.stdout, sys.stderr):
        stream.flush()
    os.dup2(log.fileno(), 1)
    os.dup2(log.fileno(), 2)
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(line_buffering=True)


def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def entry_path() -> Path:
    return data_home() / "applications" / f"{APP_ID}.desktop"


def icon_paths() -> list[Path]:
    return [data_home() / "icons" / "hicolor" / f"{size}x{size}" / "apps" / f"{APP_ID}.png" for size in ICON_SIZES]


def installed() -> bool:
    return entry_path().exists()


def exec_arg(arg: str) -> str:
    arg = arg.replace("%", "%%")
    if re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", arg):
        return arg
    inner = re.sub(r'(["`$\\])', r"\\\1", arg)
    return '"' + inner.replace("\\", "\\\\") + '"'


def install(data_dir: Path, port: int, default_port: int) -> Path:
    from PIL import Image

    source = Image.open(PKG / "static" / "icons" / "icon-512.png")
    for size, path in zip(ICON_SIZES, icon_paths()):
        path.parent.mkdir(parents=True, exist_ok=True)
        source.resize((size, size), Image.LANCZOS).save(path)
    command = ["/bin/bash", str(PROGRAM_DIR / "start.sh"), "--launch", "--data", str(data_dir)]
    if port != default_port:
        command += ["--port", str(port)]
    entry = entry_path()
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(DESKTOP_ENTRY.format(exec=" ".join(exec_arg(a) for a in command), icon=APP_ID), encoding="utf-8")
    _refresh()
    return entry


def remove() -> bool:
    found = False
    for path in [entry_path(), *icon_paths()]:
        if path.exists():
            path.unlink()
            found = True
    _refresh()
    return found


def _refresh() -> None:
    for command in (
        ["update-desktop-database", str(entry_path().parent)],
        ["gtk-update-icon-cache", "-q", "-t", str(data_home() / "icons" / "hicolor")],
    ):
        if shutil.which(command[0]):
            try:
                subprocess.run(command, capture_output=True, timeout=30)
            except (OSError, subprocess.SubprocessError):
                pass
