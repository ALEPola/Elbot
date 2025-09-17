from __future__ import annotations

import atexit
import os
from pathlib import Path
import platform
import signal
import socket
import subprocess
import time
import urllib.request
import zipfile
import tarfile

from platformdirs import user_data_dir

_env_data_dir = os.getenv("ELBOT_DATA_DIR")
if _env_data_dir:
    APP_DIR = Path(_env_data_dir).expanduser().resolve()
else:
    APP_DIR = Path(user_data_dir("Elbot", "ElbotTeam"))
BASE = APP_DIR
JAR = BASE / "Lavalink.jar"
CONF = BASE / "application.yml"
LOG = BASE / "lavalink.log"
BASE.mkdir(parents=True, exist_ok=True)

# See: https://github.com/lavalink-devs/Lavalink
LAVALINK_URL = (
    "https://github.com/lavalink-devs/Lavalink/releases/latest/download/Lavalink.jar"
)

DEFAULT_PW = os.getenv("LAVALINK_PASSWORD", "changeme")
YOUTUBE_PLUGIN_VERSION = os.getenv("LAVALINK_YOUTUBE_PLUGIN_VERSION", "1.13.5")

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


def _detect_os_arch() -> tuple[str, str]:
    sysname = platform.system().lower()
    machine = platform.machine().lower()

    # os
    if "windows" in sysname or sysname.startswith("msys") or sysname.startswith("cygwin"):
        os_name = "windows"
    elif "darwin" in sysname or "mac" in sysname:
        os_name = "mac"
    else:
        os_name = "linux"

    # arch
    if machine in ("x86_64", "amd64"):
        arch = "x64"
    elif machine in ("aarch64", "arm64"):
        arch = "aarch64"
    else:
        # best-effort default
        arch = "x64"

    return os_name, arch


def _extract_archive(archive: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    # Try zip, then tar.*
    try:
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(target_dir)
            return
    except zipfile.BadZipFile:
        pass

    try:
        with tarfile.open(archive) as tf:
            tf.extractall(target_dir)
            return
    except tarfile.TarError as e:
        raise RuntimeError(f"Unsupported JRE archive format: {archive.name}: {e}")


def _find_java_in(dir_path: Path) -> Path | None:
    # Search for bin/java or bin/java.exe under dir_path
    cand = []
    for p in dir_path.rglob("bin/java*"):
        # Prefer exact java(.exe)
        if p.name in ("java", "java.exe"):
            return p
        cand.append(p)
    return cand[0] if cand else None


def _download_jre(base: Path) -> Path:
    os_name, arch = _detect_os_arch()
    jre_dir = base / "jre"
    jre_dir.mkdir(parents=True, exist_ok=True)

    # If already present, reuse
    existing = _find_java_in(jre_dir)
    if existing and existing.exists():
        return existing

    # Adoptium (Eclipse Temurin) latest GA JRE 17 URL
    url = f"https://api.adoptium.net/v3/binary/latest/17/ga/{os_name}/{arch}/jre/hotspot/normal/eclipse"
    tmp_name = jre_dir / ("jre17.zip" if os_name == "windows" else "jre17.tar.gz")

    print(f"[auto-lavalink] Downloading OpenJDK 17 JRE ({os_name}/{arch}) ...")
    urllib.request.urlretrieve(url, tmp_name)

    # Extract into jre_dir, then locate bin/java
    _extract_archive(tmp_name, jre_dir)
    java_bin = _find_java_in(jre_dir)
    if not java_bin:
        raise RuntimeError("Downloaded JRE does not contain a java binary.")
    return java_bin


def _get_java_bin() -> str:
    from shutil import which

    j = which("java")
    if j:
        return j

    # Attempt to download a portable JRE into APP_DIR
    try:
        java_bin = _download_jre(BASE)
        return str(java_bin)
    except Exception as e:
        raise RuntimeError(
            "Java 17+ is required to run Lavalink and could not be found. "
            "Tried downloading a portable JRE automatically but failed. "
            f"Error: {e}"
        )


def _ensure_jar() -> None:
    BASE.mkdir(parents=True, exist_ok=True)
    if not JAR.exists():
        print("[auto-lavalink] Downloading Lavalink.jar ...")
        urllib.request.urlretrieve(LAVALINK_URL, JAR)


def _write_conf(port: int, password: str) -> None:


    CONF.write_text(
        f"""
lavalink:
  plugins:
    - dependency: "dev.lavalink.youtube:youtube-plugin:{YOUTUBE_PLUGIN_VERSION}"
      snapshot: false
  server:
    password: "{password}"
    sources:
      youtube: false
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


def _healthy(port: int, password: str, timeout: int = 60) -> bool:
    import http.client

    start = time.time()
    while time.time() - start < timeout:
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            conn.putrequest("GET", "/version")
            conn.putheader("Authorization", password)
            conn.endheaders()
            resp = conn.getresponse()
            if resp.status == 200:
                return True
        except Exception:
            time.sleep(1)
    return False


def start() -> tuple[int, str]:
    """Start Lavalink locally; export LAVALINK_* envs for the bot."""

    global _proc, _port
    if _proc and _proc.poll() is None:
        return _port, os.environ["LAVALINK_PASSWORD"]

    java_bin = _get_java_bin()
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
            java_bin,
            "-Xms128m",
            "-Xmx512m",
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

    if not _healthy(port, password, timeout=60):
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
