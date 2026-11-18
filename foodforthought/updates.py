from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, Request

from . import __version__, launcher
from .config import DEFAULT_PORT
from .web import Redirect, Web

ROOT = launcher.PROGRAM_DIR
FETCH_SECONDS = 90
INSTALL_SECONDS = 900


class UpdateProblem(Exception):
    pass


@dataclass
class Check:
    behind: int
    subjects: list[str]
    target: str
    checked_at: float


@dataclass
class Job:
    state: str = "running"
    steps: list[str] = field(default_factory=list)
    error: str = ""
    thread: threading.Thread | None = None


def git(*args: str, timeout: int = 60) -> str:
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"},
        )
    except FileNotFoundError as exc:
        raise UpdateProblem("Git is not installed.") from exc
    except subprocess.TimeoutExpired as exc:
        raise UpdateProblem("GitHub did not answer in time. Check the internet connection and try again.") from exc
    if done.returncode != 0:
        raise UpdateProblem(done.stderr.strip().splitlines()[-1] if done.stderr.strip() else f"git {args[0]} failed.")
    return done.stdout.strip()


def unavailable() -> str | None:
    if not (ROOT / ".git").exists():
        return "This copy was not made with git clone, so it cannot update itself. The README explains how to switch."
    try:
        git("rev-parse", "HEAD")
    except UpdateProblem as exc:
        return str(exc)
    return None


def current() -> dict:
    try:
        commit, day = git("log", "-1", "--format=%h|%cs").split("|")
    except (UpdateProblem, ValueError):
        return {"version": __version__, "commit": "", "day": ""}
    return {"version": __version__, "commit": commit, "day": day}


def upstream() -> str:
    try:
        return git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    except UpdateProblem:
        return "origin/main"


def check() -> Check:
    target = upstream()
    git("fetch", "--quiet", target.split("/", 1)[0], timeout=FETCH_SECONDS)
    subjects = [line for line in git("log", "--format=%s", f"HEAD..{target}").splitlines() if line]
    return Check(len(subjects), subjects, target, time.time())


def pip_command() -> list[str]:
    return [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-e", str(ROOT)]


def restart_process(port: int, data_dir) -> None:
    for stream in (sys.stdout, sys.stderr):
        stream.flush()
    args = [sys.executable, "-m", "foodforthought", "--no-browser", "--data", str(data_dir)]
    if port != DEFAULT_PORT:
        args += ["--port", str(port)]
    os.execv(sys.executable, args)


def apply(job: Job, target: str) -> str:
    if git("status", "--porcelain", "--untracked-files=no"):
        raise UpdateProblem("Files in the program folder have been changed by hand, so the update was not applied.")
    job.steps.append("Downloading the update")
    git("fetch", "--quiet", target.split("/", 1)[0], timeout=FETCH_SECONDS)
    old = git("rev-parse", "HEAD")
    git("merge", "--ff-only", "--quiet", target)
    new = git("rev-parse", "--short", "HEAD")
    if "pyproject.toml" in git("diff", "--name-only", old, "HEAD").splitlines():
        job.steps.append("Installing new libraries (this can take a few minutes)")
        try:
            done = subprocess.run(pip_command(), cwd=ROOT, capture_output=True, text=True, timeout=INSTALL_SECONDS)
            failed = done.returncode != 0
        except (OSError, subprocess.SubprocessError):
            failed = True
        if failed:
            git("reset", "--hard", "--quiet", old)
            raise UpdateProblem("The new libraries could not be installed, so the previous version was kept. Check the internet connection and try again.")
        marker = ROOT / ".venv" / ".installed"
        if marker.parent.is_dir():
            marker.touch()
    return new


def router(web: Web) -> APIRouter:
    r = APIRouter(dependencies=[Depends(web.require_unlocked)])
    state: dict = {"check": None, "job": None, "error": ""}


    def run(job: Job, target: str) -> None:
        try:
            new = apply(job, target)
        except UpdateProblem as exc:
            job.state, job.error = "failed", str(exc)
            return
        except Exception as exc:
            job.state, job.error = "failed", f"The update stopped unexpectedly: {exc}"
            return
        job.steps.append(f"Updated to {new}")
        try:
            web.log("app_updated", f"to {new}")
        except Exception:
            pass
        if os.name != "posix":
            job.state = "done"
            return
        job.state = "restarting"
        time.sleep(0.5)
        web.core.lock("update")
        restart_process(web.cfg.port, web.cfg.data_dir)

    def context() -> dict:
        job = state["job"]
        return {
            "about": current(),
            "unavailable": unavailable(),
            "check": state["check"],
            "check_error": state["error"],
            "job": job,
            "checked_ago": int(time.time() - state["check"].checked_at) if state["check"] else None,
        }

    @r.get("/updates")
    def page(request: Request):
        return web.page(request, "updates.html", **context())

    @r.get("/updates/status")
    def status(request: Request):
        return web.page(request, "_update_status.html", **context())

    @r.post("/updates/check")
    def do_check():
        if unavailable() or (state["job"] and state["job"].state == "running"):
            raise Redirect("/updates")
        try:
            state["check"], state["error"] = check(), ""
        except UpdateProblem as exc:
            state["check"], state["error"] = None, str(exc)
        raise Redirect("/updates")

    @r.post("/updates/apply")
    def do_apply():
        found = state["check"]
        job = state["job"]
        if unavailable() or not found or not found.behind or (job and job.state == "running"):
            raise Redirect("/updates")
        job = Job()
        job.thread = threading.Thread(target=run, args=(job, found.target), daemon=True)
        state["job"], state["check"] = job, None
        job.thread.start()
        raise Redirect("/updates")

    @r.post("/settings/launcher")
    def add_to_launcher():
        if launcher.supported():
            launcher.install(web.cfg.data_dir, web.cfg.port, DEFAULT_PORT)
            web.log("launcher_added")
        raise Redirect("/settings?msg=launcher-added#this-app")

    @r.post("/settings/launcher/remove")
    def remove_from_launcher():
        if launcher.supported():
            launcher.remove()
        raise Redirect("/settings?msg=launcher-removed#this-app")

    return r
