import os
import sys
import subprocess
from pathlib import Path
import shutil


def install_systemd_service(root_dir: Path, require_lavalink: bool = False) -> None:
    service_file = Path("/etc/systemd/system/elbot.service")
    python = sys.executable
    unit = f"""[Unit]
Description=Elbot Discord Bot
After=network.target"""
    if require_lavalink:
        unit += "\nRequires=lavalink.service\nAfter=lavalink.service"
    unit += f"""

[Service]
Type=simple
WorkingDirectory={root_dir}
ExecStart={python} -m elbot.main
EnvironmentFile={root_dir}/.env
Restart=on-failure

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
