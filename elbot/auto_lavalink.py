from __future__ import annotations

import atexit
import os
import pathlib
import signal
import socket
import subprocess
import time
import urllib.request

BASE = pathlib.Path.home() / ".elbot_lavalink"
JAR = BASE / "Lavalink.jar"
CONF = BASE / "application.yml"
LOG = BASE / "lavalink.log"

# See: https://github.com/lavalink-devs/Lavalink
LAVALINK_URL = (
    "https://github.com/lavalink-devs/Lavalink/releases/latest/download/Lavalink.jar"
)

DEFAULT_PW = os.getenv("LAVALINK_PASSWORD", "changeme")
_proc: subprocess.Popen[str] | None = None
_port: int | None = None


def _find_free_port(start: int = 2333, tries: int = 40) -> int:
    for p in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("No free TCP port available in 2333-2372")


def _ensure_java() -> None:
    from shutil import which

    j = which("java")
    if not j:
        raise RuntimeError(
            "Java 17+ is required to run Lavalink. Install OpenJDK 17 and retry "
            "(e.g., sudo apt-get install openjdk-17-jre)."
        )


def _ensure_jar() -> None:
    BASE.mkdir(parents=True, exist_ok=True)
    if not JAR.exists():
        print("[auto-lavalink] Downloading Lavalink.jar ...")
        urllib.request.urlretrieve(LAVALINK_URL, JAR)


def _write_conf(port: int, password: str) -> None:
    CONF.write_text(
        f"""server:
  address: 127.0.0.1
  port: {port}

spring:
  cloud:
    config:
      enabled: false
      import-check:
        enabled: false

lavalink:
  server:
    password: "{password}"
    sources:
      youtube: true
      soundcloud: true
      bandcamp: true
      twitch: true
      vimeo: true
      http: true
    bufferDurationMs: 400
    resamplingQuality: LOW

logging:
  file:
    path: "{LOG.as_posix()}"
"""
    )


def _healthy(port: int, timeout: int = 30) -> bool:
    import http.client

    start = time.time()
    while time.time() - start < timeout:
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            conn.request("GET", "/version")
            r = conn.getresponse()
            if r.status == 200:
                return True
        except Exception:
            time.sleep(1)
    return False


def start() -> tuple[int, str]:
    """Start Lavalink locally; export LAVALINK_* envs for the bot."""

    global _proc, _port
    if _proc and _proc.poll() is None:
        return _port, os.environ["LAVALINK_PASSWORD"]

    _ensure_java()
    _ensure_jar()

    wanted = int(os.getenv("LAVALINK_PORT", "0"))
    port = wanted if wanted > 0 else _find_free_port()
    password = os.getenv("LAVALINK_PASSWORD", DEFAULT_PW)

    _write_conf(port, password)

    env = os.environ.copy()
    BASE.mkdir(parents=True, exist_ok=True)
    # ensure log is a FILE, not a directory
    if LOG.exists() and LOG.is_dir():
        import shutil

        shutil.rmtree(LOG)

    log_fp = open(LOG, "a", buffering=1, encoding="utf-8", errors="ignore")

    # >>> KEY FIX: force Spring to load our config file <<<
    spring_loc = f"file:{CONF.as_posix()}"
    _proc = subprocess.Popen(
        [
            "java",
            f"-Dspring.config.location={spring_loc}",
            "-Dspring.cloud.config.enabled=false",
            "-Dspring.cloud.config.import-check.enabled=false",
            "-jar",
            str(JAR),
        ],
        cwd=str(BASE),
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    _port = port

    if not _healthy(port, timeout=60):
        try:
            _proc.terminate()
        except Exception:
            pass
        # show last log lines to explain the failure
        try:
            tail = "\n".join(
                open(LOG, encoding="utf-8", errors="ignore")
                .read()
                .splitlines()[-120:]
            )
        except Exception:
            tail = "<no log available>"
        raise RuntimeError("Lavalink failed healthcheck (/version). Recent log:\n" + tail)

    os.environ["LAVALINK_HOST"] = "127.0.0.1"
    os.environ["LAVALINK_PORT"] = str(port)
    os.environ["LAVALINK_PASSWORD"] = password

    print(f"[auto-lavalink] Ready on 127.0.0.1:{port}")
    atexit.register(stop)
    try:
        signal.signal(signal.SIGTERM, lambda *_: stop())
    except Exception:
        pass
    return port, password


def stop() -> None:
    """Terminate the Lavalink child cleanly."""

    global _proc
    if _proc and _proc.poll() is None:
        _proc.terminate()
        try:
            _proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _proc.kill()
    _proc = None
