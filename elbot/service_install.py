import subprocess
from pathlib import Path
import shutil
import sys
import os


def install_systemd_service(root_dir: Path, require_lavalink: bool = False) -> None:
    service_file = Path("/etc/systemd/system/elbot.service")
    python = sys.executable
    user = os.getenv("SUDO_USER") or os.getenv("USER", "root")
    token = os.getenv("DISCORD_TOKEN", "YOUR_REAL_TOKEN")
    ffmpeg = os.getenv("FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg"
    unit = f"""[Unit]
Description=Elbot Discord Bot
After=network-online.target
Wants=network-online.target"""
    if require_lavalink:
        unit += "\nRequires=lavalink.service\nAfter=lavalink.service"
    unit += f"""

[Service]
User={user}
WorkingDirectory={root_dir}
Environment=AUTO_LAVALINK=1
Environment=DISCORD_TOKEN={token}
Environment=FFMPEG_PATH={ffmpeg}
# Environment=LAVALINK_PASSWORD=changeme
# Environment=LAVALINK_PORT=2333
ExecStart={python} -m elbot.main
Restart=always
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
"""
    with open(service_file, "w") as f:
        f.write(unit)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "elbot.service"], check=True)
    subprocess.run(["systemctl", "start", "elbot.service"], check=True)
    print("Elbot systemd service installed, enabled and started.")


def install_windows_service(root_dir: Path) -> None:
    python = sys.executable
    cmd = f'"{python}" -m elbot.main'
    subprocess.run(
        [
            "sc",
            "create",
            "Elbot",
            "binPath=",
            cmd,
            "start=",
            "auto",
        ],
        check=True,
    )
    subprocess.run(["sc", "start", "Elbot"], check=True)
    print("Elbot Windows service installed, set to start automatically, and started.")


def uninstall_systemd_service() -> None:
    subprocess.run(["systemctl", "disable", "elbot.service"], check=False)
    service_file = Path("/etc/systemd/system/elbot.service")
    if service_file.exists():
        service_file.unlink()
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    print("Elbot systemd service removed.")


def uninstall_windows_service() -> None:
    subprocess.run(["sc", "stop", "Elbot"], check=False)
    subprocess.run(["sc", "delete", "Elbot"], check=False)
    print("Elbot Windows service removed.")


def main() -> None:
    remove = "--remove" in sys.argv
    require_lavalink = "--require-lavalink" in sys.argv
    root_dir = Path(__file__).resolve().parents[1]
    if os.name == "nt":
        if remove:
            uninstall_windows_service()
        else:
            install_windows_service(root_dir)
    elif shutil.which("systemctl"):
        if remove:
            uninstall_systemd_service()
        else:
            install_systemd_service(root_dir, require_lavalink=require_lavalink)
    else:
        print("Service management is not supported on this platform.")


if __name__ == "__main__":
    main()
