import subprocess
from pathlib import Path
import shutil
import sys
import os
import platform


SYSTEMD_SERVICE_FILE = Path("/etc/systemd/system/elbot.service")
LAVALINK_UNIT = "lavalink.service"


def _systemd_unit_exists(unit_name: str) -> bool:
    """Return True when a given unit file is registered with systemd."""

    try:
        result = subprocess.run(
            ["systemctl", "list-unit-files", unit_name],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError:
        return False

    if result.returncode != 0:
        return False

    return unit_name in (result.stdout or "")


def install_systemd_service(root_dir: Path, require_lavalink: bool = False) -> None:
    service_file = SYSTEMD_SERVICE_FILE
    python = sys.executable
    user = os.getenv("SUDO_USER") or os.getenv("USER", "root")
    env_file = root_dir / ".env"
    unit = """[Unit]
Description=Elbot Discord Bot
After=network-online.target
Wants=network-online.target"""
    if require_lavalink:
        if _systemd_unit_exists(LAVALINK_UNIT):
            unit += f"\nRequires={LAVALINK_UNIT}\nAfter={LAVALINK_UNIT}"
        else:
            print(
                "Warning: Lavalink systemd unit not found; continuing without Requires= dependency.",
                file=sys.stderr,
            )
            unit += f"\nWants={LAVALINK_UNIT}\nAfter={LAVALINK_UNIT}"
    unit += f"""

[Service]
User={user}
WorkingDirectory={root_dir}
EnvironmentFile={env_file}
# Environment=LAVALINK_PASSWORD=changeme
# Environment=LAVALINK_PORT=2333
ExecStart={python} -m elbot.main
Restart=always
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
"""
    service_file.write_text(unit, encoding="utf-8")
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "elbot.service"], check=True)
    subprocess.run(["systemctl", "start", "elbot.service"], check=True)
    print("Elbot systemd service installed, enabled and started.")


def install_windows_service(root_dir: Path) -> None:
    """Install Windows service via pywin32 helper."""
    python = sys.executable
    # Install with auto startup and working directory argument so .env resolves
    subprocess.run(
        [python, "-m", "elbot.win_service", "install", "--startup=auto", "--working-dir", str(root_dir)],
        check=True,
    )
    subprocess.run([python, "-m", "elbot.win_service", "start"], check=True)
    print("Elbot Windows service installed and started.")


def install_launchd_service(root_dir: Path, require_lavalink: bool = False) -> None:
    """Install a user LaunchAgent on macOS (Darwin)."""
    python = sys.executable
    label = "com.elbot.bot"
    agents = Path.home() / "Library" / "LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    plist = agents / f"{label}.plist"

    # Build EnvironmentVariables dict for launchd
    env_lines = """
        <key>EnvironmentVariables</key>
        <dict>
            <key>AUTO_LAVALINK</key><string>1</string>
        </dict>
    """.strip()

    content = f"""
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>-m</string>
        <string>elbot.main</string>
    </array>
    <key>WorkingDirectory</key><string>{root_dir}</string>
    {env_lines}
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>{root_dir}/logs/launchd.out.log</string>
    <key>StandardErrorPath</key><string>{root_dir}/logs/launchd.err.log</string>
</dict>
</plist>
"""
    plist.write_text(content)
    # Load and start the agent
    subprocess.run(["launchctl", "load", str(plist)], check=False)
    subprocess.run(["launchctl", "start", label], check=False)
    print(f"Elbot LaunchAgent installed: {plist}")


def uninstall_systemd_service() -> None:
    subprocess.run(["systemctl", "disable", "elbot.service"], check=False)
    service_file = SYSTEMD_SERVICE_FILE
    if service_file.exists():
        service_file.unlink()
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    print("Elbot systemd service removed.")


def uninstall_windows_service() -> None:
    python = sys.executable
    subprocess.run([python, "-m", "elbot.win_service", "stop"], check=False)
    subprocess.run([python, "-m", "elbot.win_service", "remove"], check=False)
    print("Elbot Windows service removed.")


def uninstall_launchd_service() -> None:
    label = "com.elbot.bot"
    plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    subprocess.run(["launchctl", "stop", label], check=False)
    subprocess.run(["launchctl", "unload", str(plist)], check=False)
    if plist.exists():
        plist.unlink()
    print("Elbot LaunchAgent removed.")


def main() -> None:
    remove = "--remove" in sys.argv
    require_lavalink = "--require-lavalink" in sys.argv
    root_dir = Path(__file__).resolve().parents[2]
    system = platform.system().lower()
    if os.name == "nt":
        if remove:
            uninstall_windows_service()
        else:
            install_windows_service(root_dir)
    elif system == "darwin":
        if remove:
            uninstall_launchd_service()
        else:
            install_launchd_service(root_dir, require_lavalink=require_lavalink)
    elif shutil.which("systemctl"):
        if remove:
            uninstall_systemd_service()
        else:
            install_systemd_service(root_dir, require_lavalink=require_lavalink)
    else:
        print("Service management is not supported on this platform.")


if __name__ == "__main__":
    main()
