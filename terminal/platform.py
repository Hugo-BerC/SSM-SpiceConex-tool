from __future__ import annotations
import os
import base64
import platform
import shutil
import shlex
import subprocess
from pathlib import Path


def _is_wsl() -> bool:
    return platform.system() == "Linux" and (bool(os.environ.get("WSL_DISTRO_NAME")) or "microsoft" in platform.release().lower())



def _wsl_interop_available() -> bool:
    """Windows executables require a registered WSLInterop binary handler."""
    return Path("/proc/sys/fs/binfmt_misc/WSLInterop").is_file()


def _windows_terminal_on_windows() -> str | None:
    direct = shutil.which("wt.exe")
    if direct:
        return direct
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        alias = Path(local_app_data) / "Microsoft" / "WindowsApps" / "wt.exe"
        if alias.is_file():
            return str(alias)
    return None


def _windows_terminal_in_wsl() -> str | None:
    """Locate the Windows Terminal app execution alias from inside WSL."""
    if not _wsl_interop_available():
        return None
    direct = shutil.which("wt.exe")
    if direct:
        return direct
    # WSL may have Windows PATH interoperability disabled, while /mnt/c remains mounted.
    candidates = []
    for base in ("/mnt/c", "/mnt/d"):
        users = Path(base) / "Users"
        if users.is_dir():
            try:
                candidates.extend(users.glob("*/AppData/Local/Microsoft/WindowsApps/wt.exe"))
            except OSError:
                pass
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    # Ask Windows itself where its execution alias is installed.
    cmd = shutil.which("cmd.exe")
    converter = shutil.which("wslpath")
    if cmd and converter:
        try:
            result = subprocess.run([cmd, "/d", "/c", "where wt.exe"],
                                    capture_output=True, text=True, timeout=5, check=False)
            for windows_path in result.stdout.splitlines():
                converted = subprocess.run([converter, "-u", windows_path.strip()],
                                           capture_output=True, text=True, timeout=3, check=False)
                path = converted.stdout.strip()
                if path and Path(path).is_file():
                    return path
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None

def terminal_candidates() -> list[str]:
    system = platform.system()
    if system == "Darwin":
        return ["osascript", "open"]
    if system == "Windows":
        return ["wt.exe", "powershell.exe"]
    if _is_wsl():
        return ["gnome-terminal", "kgx", "konsole", "xfce4-terminal", "x-terminal-emulator", "xterm"]
    return ["gnome-terminal", "kgx", "konsole", "xfce4-terminal", "x-terminal-emulator", "xterm"]


def find_terminal() -> str | None:
    for candidate in terminal_candidates():
        path = shutil.which(candidate)
        if path:
            return path
    return None


def _applescript_quote(value: str) -> str:
    escaped = (value.replace("\\", "\\\\").replace('"', '\\"')
               .replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t"))
    return '"' + escaped + '"'


def _wt_quote(value: str) -> str:
    """Escape Windows Terminal's own command separator, independently of shells."""
    return value.replace(";", "\\;")


def _xterm_arguments(terminal: str, title: str, command: str) -> list[str]:
    """Use explicit options and Xresources so a user theme cannot make xterm unreadable."""
    return [
        terminal, "-T", title, "-geometry", "150x42",
        "-bg", "#0B0A08", "-fg", "#F0E8D8", "-cr", "#D3914D", "-bd", "#4A3529",
        "-fa", "DejaVu Sans Mono", "-fs", "18",
        "-xrm", "XTerm*vt100.background: #0B0A08",
        "-xrm", "XTerm*vt100.foreground: #F0E8D8",
        "-xrm", "XTerm*faceName: DejaVu Sans Mono",
        "-xrm", "XTerm*faceSize: 18",
        "-xrm", "XTerm*selectToClipboard: true",
        "-xrm", "*VT100.translations: #override <Key>Insert: string(\"\\033[2~\")",
        "-e", "bash", "-lc", command,
    ]


def open_command(command: str, title: str | None = None):
    system = platform.system()
    window_title = title or "SSM-SpiceConex"

    if system == "Darwin":
        # Terminal.app keeps the user's normal shell (zsh) and creates a new tab
        # directly through its AppleScript API, avoiding Accessibility permissions.
        script = (
            'tell application "Terminal"\n'
            'activate\n'
            'if (count of windows) is 0 then\n'
            f'set sessionTab to do script {_applescript_quote(command)}\n'
            'else\n'
            f'set sessionTab to do script {_applescript_quote(command)} in front window\n'
            'end if\n'
            f'set custom title of sessionTab to {_applescript_quote(window_title)}\n'
            'end tell'
        )
        return subprocess.Popen(["osascript", "-e", script])

    if system == "Windows":
        encoded = base64.b64encode(command.encode("utf-16le")).decode("ascii")
        powershell_args = ["powershell.exe", "-NoExit", "-EncodedCommand", encoded]
        terminal = _windows_terminal_on_windows()
        if terminal:
            return subprocess.Popen([terminal, "-w", "0", "new-tab", "--title", _wt_quote(window_title), *powershell_args])
        terminal = shutil.which("powershell.exe")
        if not terminal:
            raise RuntimeError("No supported Windows terminal found.")
        return subprocess.Popen([terminal, *powershell_args[1:]])

    if _is_wsl():
        wt = _windows_terminal_in_wsl()
        distro = os.environ.get("WSL_DISTRO_NAME", "")
        if wt:
            # -w 0 reuses the most recently used Windows Terminal window.
            # The AWS CLI and SSO cache remain inside the selected WSL distro.
            args = [wt, "-w", "0", "new-tab", "--title", _wt_quote(window_title), "wsl.exe"]
            if distro:
                args += ["-d", _wt_quote(distro)]
            args += ["--exec", "bash", "-lc", _wt_quote(command)]
            try:
                return subprocess.Popen(args)
            except OSError:
                pass  # Fall back to an installed Linux terminal only if WT cannot launch.

    terminal = find_terminal()
    if not terminal:
        raise RuntimeError("No supported graphical terminal found.")
    name = os.path.basename(os.path.realpath(terminal)).lower()

    if name in {"xterm", "uxterm"}:
        return subprocess.Popen(_xterm_arguments(terminal, window_title, command))

    if name in {"gnome-terminal", "kgx"}:
        return subprocess.Popen([terminal, "--title", window_title, "--", "bash", "-lc", command])
    if name == "konsole":
        return subprocess.Popen([terminal, "--new-tab", "-p", "tabtitle=" + window_title, "-e", "bash", "-lc", command])
    if name == "xfce4-terminal":
        return subprocess.Popen([terminal, "--title", window_title, "--command", f"bash -lc {shlex.quote(command)}"])
    return subprocess.Popen([terminal, "-e", "bash", "-lc", command])
