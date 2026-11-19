import socket
import subprocess
import sys
import threading
import time

import pytest
import uvicorn
from PIL import Image

from foodforthought import __main__ as cli
from foodforthought import launcher, updates
from foodforthought.app import create_app
from foodforthought.config import Config
from foodforthought.web import PKG

from .conftest import FAST_KDF, PASSWORD, core_of


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    home = tmp_path / "share"
    monkeypatch.setenv("XDG_DATA_HOME", str(home))
    return home


def test_manifest_worker_and_offline_page_need_no_password(client):
    manifest = client.get("/manifest.webmanifest")
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    data = manifest.json()
    assert data["name"] == "FoodForThought" and data["display"] == "standalone" and data["start_url"] == "/"
    for icon in data["icons"]:
        width, height = map(int, icon["sizes"].split("x"))
        assert Image.open(PKG / icon["src"].removeprefix("/")).size == (width, height)
    assert {icon.get("purpose") for icon in data["icons"]} == {None, "maskable"}
    worker = client.get("/sw.js")
    assert worker.headers["content-type"].startswith("text/javascript") and '"/offline"' in worker.text
    offline = client.get("/offline")
    assert offline.status_code == 200 and "FoodForThought is not running" in offline.text and "/static/offline.js" in offline.text
    for path in ("/static/offline.js", "/static/icons/icon-192.png", "/static/icons/favicon-32.png"):
        assert client.get(path).status_code == 200
    page = client.get("/setup").text
    assert '<link rel="manifest" href="/manifest.webmanifest">' in page and "favicon-32.png" in page


def test_desktop_entry(xdg, tmp_path):
    entry = launcher.install(tmp_path / "my data", 8765, 8765)
    text = entry.read_text()
    assert entry == xdg / "applications" / "foodforthought.desktop"
    assert f'Exec=/bin/bash {launcher.PROGRAM_DIR / "start.sh"} --launch --data "{tmp_path / "my data"}"' in text
    assert "Icon=foodforthought\n" in text and "Terminal=false" in text and "Name=FoodForThought" in text
    for size, path in zip(launcher.ICON_SIZES, launcher.icon_paths()):
        assert Image.open(path).size == (size, size)
    assert launcher.installed()
    launcher.install(tmp_path / "data", 8800, 8765)
    assert "--port 8800" in entry.read_text()
    assert launcher.remove() and not entry.exists() and not any(p.exists() for p in launcher.icon_paths())
    assert not launcher.remove()


@pytest.mark.parametrize(
    "arg, expected",
    [
        ("/home/jo/fft/start.sh", "/home/jo/fft/start.sh"),
        ("/home/jo/my files", '"/home/jo/my files"'),
        ('/a"b$c`d', '"/a\\\\"b\\\\$c\\\\`d"'),
        ("/100%", "/100%%"),
    ],
)
def test_exec_quoting(arg, expected):
    assert launcher.exec_arg(arg) == expected


def test_launcher_buttons_in_settings(unlocked, xdg):
    page = unlocked.get("/settings").text
    assert "This app" in page and "Version 0.8.2" in page and "Add to the launcher" in page
    done = unlocked.post("/settings/launcher")
    assert done.headers["location"] == "/settings?msg=launcher-added#this-app"
    assert (xdg / "applications" / "foodforthought.desktop").exists()
    page = unlocked.get("/settings?msg=launcher-added").text
    assert "Remove from the launcher" in page and "Linux apps folder" in page
    unlocked.post("/settings/launcher/remove")
    assert not (xdg / "applications" / "foodforthought.desktop").exists()


def test_command_line_install_and_launch(xdg, tmp_path, monkeypatch, capsys):
    cli.main(["--install-app", "--data", str(tmp_path / "d")])
    assert (xdg / "applications" / "foodforthought.desktop").exists() and "now in the launcher" in capsys.readouterr().out
    cli.main(["--remove-app"])
    assert not (xdg / "applications" / "foodforthought.desktop").exists()

    opened = []
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_port_free", lambda port: False)
    monkeypatch.setattr(launcher, "open_browser", opened.append)
    monkeypatch.setattr(launcher, "is_running", lambda port: True)
    cli.main(["--launch", "--data", str(tmp_path / "d")])
    assert opened == ["http://localhost:8765"]
    with pytest.raises(SystemExit):
        cli.main(["--data", str(tmp_path / "d")])
    assert "already running" in capsys.readouterr().out
    monkeypatch.setattr(launcher, "is_running", lambda port: False)
    with pytest.raises(SystemExit):
        cli.main(["--launch", "--data", str(tmp_path / "d")])
    assert "in use by another program" in capsys.readouterr().out and len(opened) == 1


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_is_running_recognises_the_app(tmp_path):
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(Config(tmp_path / "d", port=port, kdf=FAST_KDF)), host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert launcher.is_running(port)
        assert not launcher.is_running(free_port())
    finally:
        server.should_exit = True
        thread.join(5)


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "init.defaultBranch=main", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def repos(tmp_path, monkeypatch):
    origin, seed, local = tmp_path / "origin.git", tmp_path / "seed", tmp_path / "program"
    git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    git(tmp_path, "clone", "-q", str(origin), str(seed))
    (seed / "pyproject.toml").write_text("deps = 1\n")
    (seed / "notes.txt").write_text("one\n")
    git(seed, "add", ".")
    git(seed, "commit", "-q", "-m", "First version")
    git(seed, "push", "-q", "origin", "HEAD:main")
    git(tmp_path, "clone", "-q", str(origin), str(local))
    (local / ".venv").mkdir()
    monkeypatch.setattr(updates, "ROOT", local)
    restarts = []
    monkeypatch.setattr(updates, "restart_process", lambda port, data: restarts.append((port, data)))
    monkeypatch.setattr(updates, "pip_command", lambda: [sys.executable, "-c", f"open({str(tmp_path / 'pip-ran')!r}, 'w')"])
    return {"seed": seed, "local": local, "restarts": restarts, "tmp": tmp_path}


def push(seed, name, text, message):
    (seed / name).write_text(text)
    git(seed, "commit", "-q", "-am", message)
    git(seed, "push", "-q", "origin", "HEAD:main")


def wait_for_job(client):
    for _ in range(200):
        page = client.get("/updates/status", follow_redirects=False)
        if page.status_code != 200 or not ("Updating…" in page.text or "Restarting…" in page.text):
            return page
        time.sleep(0.05)
    raise AssertionError("update did not finish")


def test_not_a_git_copy(unlocked, tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "ROOT", tmp_path)
    page = unlocked.get("/updates").text
    assert "was not made with git clone" in page and "Check for updates" not in page
    assert unlocked.post("/updates/check").headers["location"] == "/updates"


def test_check_and_update(unlocked, repos, cfg):
    page = unlocked.get("/updates").text
    assert "Check for updates" in page and "This is version 0.8.2 (" in page
    unlocked.post("/updates/check")
    assert "You have the latest version." in unlocked.get("/updates").text
    assert unlocked.post("/updates/apply").headers["location"] == "/updates"
    assert not repos["restarts"]

    push(repos["seed"], "notes.txt", "two\n", "Add recipes")
    push(repos["seed"], "notes.txt", "three\n", "Add meal plans")
    unlocked.post("/updates/check")
    page = unlocked.get("/updates").text
    assert "2 updates available" in page and "<li>Add meal plans</li>" in page and "Update and restart" in page

    unlocked.post("/updates/apply")
    wait_for_job(unlocked)
    assert (repos["local"] / "notes.txt").read_text() == "three\n"
    assert repos["restarts"] == [(cfg.port, cfg.data_dir)]
    assert not (repos["tmp"] / "pip-ran").exists()
    assert not core_of(unlocked).unlocked
    assert unlocked.get("/updates").headers["location"].startswith("/unlock")
    unlocked.post("/unlock", data={"password": PASSWORD})
    log = unlocked.get("/audit").text
    assert "app_updated" in log


def test_new_libraries_are_installed(unlocked, repos):
    push(repos["seed"], "pyproject.toml", "deps = 2\n", "New library")
    unlocked.post("/updates/check")
    unlocked.post("/updates/apply")
    wait_for_job(unlocked)
    assert (repos["tmp"] / "pip-ran").exists() and (repos["local"] / ".venv" / ".installed").exists()
    assert repos["restarts"]


def test_failed_install_keeps_the_old_version(unlocked, repos, monkeypatch):
    before = git(repos["local"], "rev-parse", "HEAD")
    push(repos["seed"], "pyproject.toml", "deps = 3\n", "Broken library")
    monkeypatch.setattr(updates, "pip_command", lambda: [sys.executable, "-c", "raise SystemExit(1)"])
    unlocked.post("/updates/check")
    unlocked.post("/updates/apply")
    page = wait_for_job(unlocked).text
    assert "The update did not work" in page and "previous version was kept" in page
    assert git(repos["local"], "rev-parse", "HEAD") == before and not repos["restarts"]
    assert core_of(unlocked).unlocked
    assert "Check for updates" in unlocked.get("/updates").text


def test_hand_edited_files_block_the_update(unlocked, repos):
    push(repos["seed"], "notes.txt", "two\n", "Change")
    (repos["local"] / "notes.txt").write_text("edited\n")
    unlocked.post("/updates/check")
    unlocked.post("/updates/apply")
    assert "changed by hand" in wait_for_job(unlocked).text
    assert (repos["local"] / "notes.txt").read_text() == "edited\n" and not repos["restarts"]


def test_check_without_a_connection(unlocked, repos):
    git(repos["local"], "remote", "set-url", "origin", str(repos["tmp"] / "missing.git"))
    unlocked.post("/updates/check")
    page = unlocked.get("/updates").text
    assert "Could not check:" in page and "Update and restart" not in page


def test_updates_need_unlocking(client):
    assert client.get("/updates").headers["location"] in ("/setup", "/unlock")
    assert client.post("/updates/check").status_code in (303, 204)


def test_open_browser_retries_and_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(launcher.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(launcher, "_tool", lambda name: f"/usr/bin/{name}" if name == "garcon-url-handler" else None)
    results = iter([False, False, True])
    calls = []
    monkeypatch.setattr(launcher, "_run", lambda command: calls.append(command) or next(results))
    assert launcher.open_browser("http://localhost:8765")
    assert calls[0] == ["/usr/bin/garcon-url-handler", "http://localhost:8765"] and len(calls) == 3
    log = launcher.launch_log().read_text()
    assert "could not open http://localhost:8765 yet (attempt 2)" in log and "with garcon-url-handler (attempt 3)" in log

    monkeypatch.setattr(launcher, "_run", lambda command: False)
    assert not launcher.open_browser("http://localhost:8765", attempts=2)

    opened = []
    monkeypatch.setattr(launcher, "_tool", lambda name: None)
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url) or True)
    assert launcher.open_browser("http://localhost:8765") and opened == ["http://localhost:8765"]


def test_launch_log_is_kept_short(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for n in range(launcher.LOG_LINES + 50):
        launcher.note(f"line {n}")
    lines = launcher.launch_log().read_text().splitlines()
    assert len(lines) == launcher.LOG_LINES + 1 and lines[-1].endswith(f"line {launcher.LOG_LINES + 49}")


def test_wait_until_running(monkeypatch):
    answers = iter([False, False, True])
    monkeypatch.setattr(launcher, "is_running", lambda port: next(answers))
    monkeypatch.setattr(launcher.time, "sleep", lambda seconds: None)
    assert launcher.wait_until_running(8765)
    monkeypatch.setattr(launcher, "is_running", lambda port: False)
    assert not launcher.wait_until_running(8765, seconds=0.05, pause=0.01)


def test_icons_are_only_rewritten_after_a_change(xdg, tmp_path):
    launcher.install(tmp_path / "data", 8765, 8765)
    largest = launcher.icon_paths()[-1]
    old = largest.stat().st_mtime - 100
    import os
    os.utime(largest, (old + 200, old + 200))
    launcher.refresh_icons()
    assert largest.stat().st_mtime == old + 200
    os.utime(largest, (1, 1))
    launcher.refresh_icons()
    assert largest.stat().st_mtime > 1
