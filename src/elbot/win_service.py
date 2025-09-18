"""
Windows Service wrapper for running Elbot as a background service.

Requires pywin32. Installs via:
  python -m elbot.win_service install --startup=auto --working-dir <repo_root>
Then start with:
  python -m elbot.win_service start
Uninstall with:
  python -m elbot.win_service remove
"""
from __future__ import annotations

import os
import sys
import time
import subprocess

try:
    import win32event
    import win32service
    import win32serviceutil
    import servicemanager
except Exception as e:  # pragma: no cover - only on Windows
    raise SystemExit(
        "pywin32 is required to run Elbot as a Windows service."
    ) from e


class ElbotService(win32serviceutil.ServiceFramework):  # type: ignore[misc]
    _svc_name_ = "Elbot"
    _svc_display_name_ = "Elbot Discord Bot"
    _svc_description_ = (
        "Elbot runs a Nextcord-based Discord bot with optional Lavalink auto-launch."
    )

    def __init__(self, args):  # pragma: no cover - service entry
        super().__init__(args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
        self.proc: subprocess.Popen[str] | None = None
        self.workdir = None
        # Parse optional --working-dir from service command line args
        for i, a in enumerate(sys.argv):
            if a == "--working-dir" and i + 1 < len(sys.argv):
                self.workdir = sys.argv[i + 1]

    def SvcDoRun(self):  # pragma: no cover - service entry
        try:
            if self.workdir:
                os.chdir(self.workdir)
        except Exception:
            pass

        py = sys.executable
        cmd = [py, "-m", "elbot.main"]
        try:
            self.proc = subprocess.Popen(cmd, cwd=self.workdir or None)
        except Exception as e:
            # Log to Windows event log
            servicemanager.LogErrorMsg(f"Failed to start Elbot process: {e}")
            return

        # Wait loop until stop signaled
        while True:
            rc = win32event.WaitForSingleObject(self.hWaitStop, 1000)
            if rc == win32event.WAIT_OBJECT_0:
                break
            # If child died, exit (SCM will restart if configured)
            if self.proc and self.proc.poll() is not None:
                break

    def SvcStop(self):  # pragma: no cover - service entry
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        try:
            if self.proc and self.proc.poll() is None:
                try:
                    self.proc.terminate()
                    for _ in range(10):
                        if self.proc.poll() is not None:
                            break
                        time.sleep(0.5)
                    if self.proc.poll() is None:
                        self.proc.kill()
                except Exception:
                    pass
        finally:
            win32event.SetEvent(self.hWaitStop)


def main():  # pragma: no cover - service entry
    win32serviceutil.HandleCommandLine(ElbotService)


if __name__ == "__main__":  # pragma: no cover
    main()

