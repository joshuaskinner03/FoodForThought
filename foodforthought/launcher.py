from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from datetime import datetime
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


LOG_LINES = 300
SERVICE = "foodforthought.service"
RECENT_SECONDS = 45
SERVICE_UNIT = """[Unit]
Description=FoodForThought

[Service]
Type=simple
ExecStart={exec}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
"""


def launch_log() -> Path:
    return Path.home() / ".cache" / "foodforthought-launch.log"


def note(message: str) -> None:
    path = launch_log()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = path.read_text(encoding="utf-8").splitlines()[-LOG_LINES:] if path.exists() else []
        lines.append(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def wait_until_running(port: int, seconds: float = 60, pause: float = 0.5) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if is_running(port):
            return True
        time.sleep(pause)
    return False


def _tool(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    fallback = Path("/usr/bin") / name
    return str(fallback) if fallback.exists() else None


def _run(command: list[str], env: dict | None = None) -> bool:
    try:
        return subprocess.run(command, capture_output=True, timeout=20, env=env).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def opened_stamp() -> Path:
    return Path.home() / ".cache" / "foodforthought-opened"


def opened_recently(seconds: float = RECENT_SECONDS) -> bool:
    try:
        return time.time() - opened_stamp().stat().st_mtime < seconds
    except OSError:
        return False


def _opened(how: str, url: str, attempt: int) -> bool:
    note(f"opened {url} with {how} (attempt {attempt})")
    try:
        opened_stamp().parent.mkdir(parents=True, exist_ok=True)
        opened_stamp().touch()
    except OSError:
        pass
    return True


def open_browser(url: str, attempts: int = 20, pause: float = 3.0) -> bool:
    for attempt in range(1, attempts + 1):
        for name in ("garcon-url-handler", "xdg-open"):
            tool = _tool(name)
            if tool and _run([tool, url]):
                return _opened(name, url, attempt)
        if not _tool("garcon-url-handler") and not _tool("xdg-open"):
            try:
                if webbrowser.open(url):
                    return _opened("the default browser", url, attempt)
            except Exception as exc:
                note(f"browser error: {exc}")
        note(f"could not open {url} yet (attempt {attempt})")
        time.sleep(pause)
    return False


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


def write_icons() -> None:
    from PIL import Image

    source = Image.open(PKG / "static" / "icons" / "icon-512.png")
    for size, path in zip(ICON_SIZES, icon_paths()):
        path.parent.mkdir(parents=True, exist_ok=True)
        source.resize((size, size), Image.LANCZOS).save(path)


def refresh_icons() -> None:
    if not (supported() and installed()):
        return
    source = PKG / "static" / "icons" / "icon-512.png"
    largest = icon_paths()[-1]
    try:
        if largest.exists() and largest.stat().st_mtime >= source.stat().st_mtime:
            return
        write_icons()
        _refresh()
    except OSError:
        pass


def install(data_dir: Path, port: int, default_port: int) -> Path:
    write_icons()
    command = ["/bin/bash", str(PROGRAM_DIR / "start.sh"), "--launch", "--data", str(data_dir)]
    if port != default_port:
        command += ["--port", str(port)]
    entry = entry_path()
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(DESKTOP_ENTRY.format(exec=" ".join(exec_arg(a) for a in command), icon=APP_ID), encoding="utf-8")
    _refresh()
    install_service(data_dir, port, default_port)
    return entry


def service_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user" / SERVICE


def service_installed() -> bool:
    return service_path().exists()


def unit_arg(arg: str) -> str:
    arg = arg.replace("%", "%%")
    if re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", arg):
        return arg
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'


def session_env() -> dict:
    env = dict(os.environ)
    runtime = Path(f"/run/user/{os.getuid()}")
    if not env.get("XDG_RUNTIME_DIR") and runtime.is_dir():
        env["XDG_RUNTIME_DIR"] = str(runtime)
    if not env.get("DBUS_SESSION_BUS_ADDRESS") and env.get("XDG_RUNTIME_DIR"):
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={env['XDG_RUNTIME_DIR']}/bus"
    return env


def systemctl(*args: str) -> bool:
    tool = _tool("systemctl")
    return bool(tool) and _run([tool, "--user", *args], session_env())


def install_service(data_dir: Path, port: int, default_port: int) -> bool:
    command = ["/bin/bash", str(PROGRAM_DIR / "start.sh"), "--service", "--data", str(data_dir)]
    if port != default_port:
        command += ["--port", str(port)]
    path = service_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(SERVICE_UNIT.format(exec=" ".join(unit_arg(a) for a in command)), encoding="utf-8")
    except OSError as exc:
        note(f"could not write the service: {exc}")
        return False
    done = systemctl("daemon-reload") and systemctl("enable", SERVICE)
    note("service enabled: starts with Linux" if done else "service written, but systemctl could not enable it")
    return done


def ensure_service(data_dir: Path, port: int, default_port: int) -> None:
    if supported() and installed() and not (service_installed() and systemctl("is-enabled", SERVICE)):
        install_service(data_dir, port, default_port)


def start_service() -> bool:
    return service_installed() and systemctl("start", SERVICE)


def remove() -> bool:
    found = False
    if service_installed():
        systemctl("disable", SERVICE)
        service_path().unlink()
        systemctl("daemon-reload")
        found = True
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
