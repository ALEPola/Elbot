from __future__ import annotations

import atexit
import json
import os
import sys
import platform
import re
import signal
import socket
import subprocess
import time
import urllib.request
import zipfile
import tarfile
from pathlib import Path

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
LAVALINK_URL_FILE = BASE / "lavalink.url"
BASE.mkdir(parents=True, exist_ok=True)

# Lavalink 4.0.6 is the latest build that Mafic 2.x has been exercised with.
# We pin to that version by default and allow overrides via environment vars so
# operators can explicitly opt into newer releases once Mafic adds support.
DEFAULT_LAVALINK_VERSION = "4.0.6"
DEFAULT_LAVALINK_URL = (
    "https://github.com/lavalink-devs/Lavalink/releases/download/"
    f"{DEFAULT_LAVALINK_VERSION}/Lavalink.jar"
)
LAVALINK_URL = os.getenv("LAVALINK_DOWNLOAD_URL", DEFAULT_LAVALINK_URL)
# Warn when Lavalink reports a newer version than Mafic officially supports.
MAFIC_MAX_SUPPORTED_LAVALINK_VERSION = os.getenv(
    "MAFIC_MAX_SUPPORTED_LAVALINK_VERSION", DEFAULT_LAVALINK_VERSION
)

DEFAULT_PW = os.getenv("LAVALINK_PASSWORD", "changeme")
MINIMUM_YOUTUBE_PLUGIN_VERSION = "1.13.5"
DEFAULT_YOUTUBE_PLUGIN_VERSION = os.getenv(
    "LAVALINK_DEFAULT_YOUTUBE_PLUGIN_VERSION",
    MINIMUM_YOUTUBE_PLUGIN_VERSION,
)
YOUTUBE_PLUGIN_METADATA_URL = (
    "https://maven.lavalink.dev/releases/dev/lavalink/youtube/youtube-plugin/maven-metadata.xml"
)

AUTO_LAVALINK_PORT_START = int(os.getenv("AUTO_LAVALINK_PORT_START", "2333"))
AUTO_LAVALINK_PORT_TRIES = max(1, int(os.getenv("AUTO_LAVALINK_PORT_TRIES", "40")))


def _version_tuple(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for raw in re.split(r"[._-]", value):
        if not raw:
            continue
        if raw.isdigit():
            parts.append(int(raw))
        else:
            return ()
    return tuple(parts)


def _version_less_than(candidate: str, minimum: str) -> bool:
    cand = _version_tuple(candidate)
    required = _version_tuple(minimum)
    if not cand or not required:
        return False
    length = max(len(cand), len(required))
    cand += (0,) * (length - len(cand))
    required += (0,) * (length - len(required))
    return cand < required


def _fetch_latest_youtube_plugin_version() -> str | None:
    try:
        with urllib.request.urlopen(YOUTUBE_PLUGIN_METADATA_URL, timeout=10) as resp:
            data = resp.read().decode("utf-8", "ignore")
    except Exception as exc:
        print(
            "[auto-lavalink] WARNING: Failed to resolve latest youtube plugin version "
            f"from {YOUTUBE_PLUGIN_METADATA_URL}: {exc}.",
            file=sys.stderr,
        )
        return None
    for tag in ("release", "latest"):
        match = re.search(rf"<{tag}>([^<]+)</{tag}>", data)
        if match:
            return match.group(1).strip()
    matches = re.findall(r"<version>([^<]+)</version>", data)
    if matches:
        return matches[-1].strip()
    return None


def _determine_youtube_plugin_version() -> str:
    configured = os.getenv("LAVALINK_YOUTUBE_PLUGIN_VERSION")
    if configured:
        return configured
    latest = _fetch_latest_youtube_plugin_version()
    if latest and not _version_less_than(latest, MINIMUM_YOUTUBE_PLUGIN_VERSION):
        return latest
    if latest:
        print(
            f"[auto-lavalink] WARNING: Resolved youtube plugin version {latest!r} is below the minimum supported; falling back.",
            file=sys.stderr,
        )
    else:
        print(
            f"[auto-lavalink] WARNING: Falling back to default youtube plugin version {DEFAULT_YOUTUBE_PLUGIN_VERSION!r} (metadata unavailable).",
            file=sys.stderr,
        )
    return DEFAULT_YOUTUBE_PLUGIN_VERSION


YOUTUBE_PLUGIN_VERSION = _determine_youtube_plugin_version()

if _version_less_than(YOUTUBE_PLUGIN_VERSION, MINIMUM_YOUTUBE_PLUGIN_VERSION):
    print(f"[auto-lavalink] WARNING: youtube-source plugin version {YOUTUBE_PLUGIN_VERSION!r} is below the minimum supported {MINIMUM_YOUTUBE_PLUGIN_VERSION}.", file=sys.stderr)

_proc: subprocess.Popen[str] | None = None
_port: int | None = None


def _find_free_port(start: int | None = None, tries: int | None = None) -> int:
    start = AUTO_LAVALINK_PORT_START if start is None else start
    tries = AUTO_LAVALINK_PORT_TRIES if tries is None else max(1, tries)
    end = start + max(tries - 1, 0)

    for p in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError(f"No free TCP port available in {start}-{end}")


def _is_port_in_use(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    """Return True if something is listening on the given host:port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


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


def _check_java_version(java_bin: str) -> bool:
    """Check if Java binary is version 17 or higher."""
    try:
        result = subprocess.run(
            [java_bin, "-version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        # Java prints version to stderr
        output = result.stderr + result.stdout
        # Look for version pattern like "17.0.16" or "21.0.1"
        match = re.search(r'version "(\d+)(?:\.(\d+))?', output)
        if match:
            major = int(match.group(1))
            return major >= 17
    except Exception:
        pass
    return False


def _get_java_bin() -> str:
    from shutil import which

    # Common Java installation paths on Linux systems
    common_paths = [
        "/usr/bin/java",
        "/usr/lib/jvm/java-17-openjdk-arm64/bin/java",
        "/usr/lib/jvm/java-17-openjdk-amd64/bin/java",
        "/usr/lib/jvm/default-java/bin/java",
    ]

    # First try which() - works if PATH is set correctly
    j = which("java")
    if j and _check_java_version(j):
        return j

    # Try common installation paths directly
    for path in common_paths:
        if os.path.exists(path) and _check_java_version(path):
            return path

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
    cached_url = ""
    if LAVALINK_URL_FILE.exists():
        try:
            cached_url = LAVALINK_URL_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            cached_url = ""

    if not JAR.exists() or cached_url != LAVALINK_URL:
        if JAR.exists():
            try:
                JAR.unlink()
            except OSError:
                pass
        print("[auto-lavalink] Downloading Lavalink.jar ...")
        urllib.request.urlretrieve(LAVALINK_URL, JAR)
        try:
            LAVALINK_URL_FILE.write_text(LAVALINK_URL, encoding="utf-8")
        except OSError:
            pass


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


def _fetch_lavalink_version(port: int, password: str) -> str | None:
    import http.client

    conn: http.client.HTTPConnection | None = None
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.putrequest("GET", "/version")
        conn.putheader("Authorization", password)
        conn.endheaders()
        resp = conn.getresponse()
        if resp.status != 200:
            return None
        raw = resp.read()
        if not raw:
            return None
        data = json.loads(raw.decode("utf-8"))
        version = data.get("version")
        return str(version) if version else None
    except Exception:
        return None
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def _parse_version_tuple(text: str) -> tuple[int, ...]:
    numbers = []
    for part in text.split("."):
        match = re.match(r"(\d+)", part)
        if not match:
            break
        numbers.append(int(match.group(1)))
    return tuple(numbers)


def _version_is_newer(version: str, reference: str) -> bool:
    ver_tuple = _parse_version_tuple(version)
    ref_tuple = _parse_version_tuple(reference)
    if not ver_tuple or not ref_tuple:
        return False
    length = max(len(ver_tuple), len(ref_tuple))
    ver_tuple += (0,) * (length - len(ver_tuple))
    ref_tuple += (0,) * (length - len(ref_tuple))
    return ver_tuple > ref_tuple


def _warn_if_version_exceeds(version: str) -> None:
    if not MAFIC_MAX_SUPPORTED_LAVALINK_VERSION:
        return
    if _version_is_newer(version, MAFIC_MAX_SUPPORTED_LAVALINK_VERSION):
        print(
            "[auto-lavalink] WARNING: Lavalink reports version"
            f" {version} which exceeds the configured Mafic-supported"
            f" maximum ({MAFIC_MAX_SUPPORTED_LAVALINK_VERSION})."
            " Consider updating Mafic before relying on this release."
        )


def start() -> tuple[int, str]:
    """Start Lavalink locally; export LAVALINK_* envs for the bot."""

    global _proc, _port
    if _proc and _proc.poll() is None:
        if _port is None:
            raise RuntimeError("Lavalink process is running but no port was recorded")
        return _port, os.environ["LAVALINK_PASSWORD"]

    java_bin = _get_java_bin()
    _ensure_jar()

    wanted = int(os.getenv("LAVALINK_PORT", "0"))
    if wanted > 0:
        # If the user explicitly configured a port, prefer it — unless it's
        # already in use on localhost. In that case pick a free port to avoid
        # failing startup (helps when other services occupy common ports).
        if _is_port_in_use(wanted):
            print(f"[auto-lavalink] WARNING: requested LAVALINK_PORT={wanted} is already in use; selecting a free port instead.")
            port = _find_free_port()
        else:
            port = wanted
    else:
        port = _find_free_port()
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
            f"-Dserver.port={port}",
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

    reported_version = _fetch_lavalink_version(port, password)
    if reported_version:
        _warn_if_version_exceeds(reported_version)

    connect_host = os.getenv("LAVALINK_HOST", "0.0.0.0")
    if connect_host == "0.0.0.0":
        connect_host = "127.0.0.1"
    os.environ["LAVALINK_HOST"] = connect_host
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
