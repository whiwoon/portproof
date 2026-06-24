#!/usr/bin/env python3
"""
Minimal Windows GUI evidence capture spike.

Python 3 stdlib only. No Playwright, Selenium, Pillow, pywin32, or pip packages.
The script launches real GUI windows and captures them through a generated
PowerShell/.NET helper.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


DEFAULT_WAIT_SECONDS = 8
DEFAULT_EDGE_WAIT_SECONDS = 10
EDGE_SIZE = "1366,768"


def sanitize_evidence_part(value: str) -> str:
    safe = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
    safe = "-".join(part for part in safe.split("-") if part)
    return safe or "unknown"


CAPTURE_PS1 = r'''
param(
    [Parameter(Mandatory=$true)][string]$OutFile,
    [int]$ProcessId = 0,
    [string]$ProcessName = "",
    [string]$TitleContains = "",
    [int]$TimeoutSeconds = 20,
    [int]$SetForeground = 1,
    [int]$PreferPrintWindow = 0,
    [int]$WindowX = 40,
    [int]$WindowY = 40,
    [int]$WindowWidth = 1200,
    [int]$WindowHeight = 720
)

Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public static class WinCapNative {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")]
    public static extern bool EnumWindows(EnumWindowsProc enumProc, IntPtr lParam);

    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);

    [DllImport("user32.dll")]
    public static extern int GetWindowTextLength(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);

    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);

    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    [DllImport("user32.dll")]
    public static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int nWidth, int nHeight, bool bRepaint);

    [DllImport("user32.dll")]
    public static extern bool PrintWindow(IntPtr hwnd, IntPtr hdcBlt, uint nFlags);

    [DllImport("user32.dll")]
    public static extern IntPtr GetDC(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern IntPtr GetWindowDC(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern int ReleaseDC(IntPtr hWnd, IntPtr hDC);

    [DllImport("gdi32.dll")]
    public static extern bool BitBlt(IntPtr hdcDest, int nXDest, int nYDest, int nWidth, int nHeight,
        IntPtr hdcSrc, int nXSrc, int nYSrc, int dwRop);

    public const int SRCCOPY = 0x00CC0020;
    public const int SW_RESTORE = 9;

    [StructLayout(LayoutKind.Sequential)]
    public struct RECT {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    public class WindowInfo {
        public IntPtr Handle;
        public uint ProcessId;
        public string Title;
        public int Width;
        public int Height;
    }

    public static List<WindowInfo> FindWindows(int wantedPid, string wantedTitle) {
        List<WindowInfo> result = new List<WindowInfo>();
        EnumWindows(delegate(IntPtr hWnd, IntPtr lParam) {
            if (!IsWindowVisible(hWnd)) return true;

            int len = GetWindowTextLength(hWnd);
            if (len <= 0) return true;

            StringBuilder title = new StringBuilder(len + 1);
            GetWindowText(hWnd, title, title.Capacity);

            uint pid;
            GetWindowThreadProcessId(hWnd, out pid);

            RECT rect;
            if (!GetWindowRect(hWnd, out rect)) return true;

            int width = rect.Right - rect.Left;
            int height = rect.Bottom - rect.Top;
            if (width < 50 || height < 50) return true;

            if (wantedPid > 0 && pid != (uint)wantedPid) return true;
            if (!String.IsNullOrEmpty(wantedTitle) &&
                title.ToString().IndexOf(wantedTitle, StringComparison.OrdinalIgnoreCase) < 0) return true;

            result.Add(new WindowInfo {
                Handle = hWnd,
                ProcessId = pid,
                Title = title.ToString(),
                Width = width,
                Height = height
            });
            return true;
        }, IntPtr.Zero);
        return result;
    }
}
"@

function Get-MatchingWindow {
    param(
        [int]$WantedPid,
        [string]$WantedProcessName,
        [string]$WantedTitle,
        [int]$Timeout
    )

    $deadline = (Get-Date).AddSeconds($Timeout)
    while ((Get-Date) -lt $deadline) {
        $pidCandidates = @()
        if ($WantedPid -gt 0) {
            $pidCandidates += $WantedPid
            try {
                $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$WantedPid"
                foreach ($child in $children) { $pidCandidates += [int]$child.ProcessId }
            } catch {}
        }

        $windows = @()
        if ($pidCandidates.Count -gt 0) {
            foreach ($candidatePid in ($pidCandidates | Select-Object -Unique)) {
                $windows += [WinCapNative]::FindWindows($candidatePid, $WantedTitle)
            }
        } else {
            $windows = [WinCapNative]::FindWindows(0, $WantedTitle)
        }

        if ($WantedProcessName) {
            $windows = @($windows | Where-Object {
                try {
                    (Get-Process -Id $_.ProcessId -ErrorAction Stop).ProcessName -ieq $WantedProcessName
                } catch {
                    $false
                }
            })
        }

        if ($windows.Count -eq 0 -and $WantedTitle) {
            # Windows 11 can host cmd.exe in Windows Terminal. In that case the
            # visible top-level window belongs to WindowsTerminal.exe, not the
            # launched cmd.exe PID or its child process tree.
            $windows = [WinCapNative]::FindWindows(0, $WantedTitle)
        }

        if ($windows.Count -gt 0) {
            return ($windows | Sort-Object Width, Height -Descending | Select-Object -First 1)
        }

        Start-Sleep -Milliseconds 250
    }
    return $null
}

$window = Get-MatchingWindow -WantedPid $ProcessId -WantedProcessName $ProcessName -WantedTitle $TitleContains -Timeout $TimeoutSeconds
if ($null -eq $window) {
    throw "No matching visible top-level window found. pid=$ProcessId process=$ProcessName titleContains='$TitleContains'"
}

$rect = New-Object WinCapNative+RECT
$moved = $false
if ($SetForeground -ne 0) {
    [void][WinCapNative]::ShowWindow($window.Handle, [WinCapNative]::SW_RESTORE)
    if ($WindowWidth -gt 0 -and $WindowHeight -gt 0) {
        $moved = [WinCapNative]::MoveWindow($window.Handle, $WindowX, $WindowY, $WindowWidth, $WindowHeight, $true)
    }
    [void][WinCapNative]::SetForegroundWindow($window.Handle)
    Start-Sleep -Milliseconds 700
}

[void][WinCapNative]::GetWindowRect($window.Handle, [ref]$rect)
$width = $rect.Right - $rect.Left
$height = $rect.Bottom - $rect.Top
if ($width -le 0 -or $height -le 0) {
    throw "Matched window has invalid size: ${width}x${height}"
}

if ($PreferPrintWindow -ne 0) {
    # Keep browser captures in a predictable visible area. VMware/remote sessions
    # can return a successful-but-black PrintWindow bitmap for Chromium windows;
    # making the window visible before fallback screen capture improves results.
    if ($WindowWidth -gt 0 -and $WindowHeight -gt 0) {
        $moved = [WinCapNative]::MoveWindow($window.Handle, $WindowX, $WindowY, $WindowWidth, $WindowHeight, $true)
    }
    Start-Sleep -Milliseconds 500
    [void][WinCapNative]::GetWindowRect($window.Handle, [ref]$rect)
    $width = $rect.Right - $rect.Left
    $height = $rect.Bottom - $rect.Top
}

function Test-BitmapMostlyBlack {
    param(
        [Parameter(Mandatory=$true)][System.Drawing.Bitmap]$Bitmap
    )

    $stepX = [Math]::Max(1, [int]($Bitmap.Width / 40))
    $stepY = [Math]::Max(1, [int]($Bitmap.Height / 40))
    $total = 0
    $dark = 0

    for ($y = 0; $y -lt $Bitmap.Height; $y += $stepY) {
        for ($x = 0; $x -lt $Bitmap.Width; $x += $stepX) {
            $pixel = $Bitmap.GetPixel($x, $y)
            $brightness = ([int]$pixel.R + [int]$pixel.G + [int]$pixel.B) / 3
            if ($brightness -lt 12) { $dark++ }
            $total++
        }
    }

    if ($total -eq 0) { return $false }
    return (($dark / $total) -gt 0.97)
}

function New-WindowBitmap {
    param([int]$BitmapWidth, [int]$BitmapHeight)
    return New-Object System.Drawing.Bitmap($BitmapWidth, $BitmapHeight)
}

function Copy-WindowFromScreen {
    param(
        [Parameter(Mandatory=$true)][System.Drawing.Bitmap]$TargetBitmap,
        [Parameter(Mandatory=$true)]$WindowRect
    )

    $graphics = [System.Drawing.Graphics]::FromImage($TargetBitmap)
    $dest = $graphics.GetHdc()
    $screen = [WinCapNative]::GetDC([IntPtr]::Zero)
    $ok = [WinCapNative]::BitBlt($dest, 0, 0, $TargetBitmap.Width, $TargetBitmap.Height, $screen, $WindowRect.Left, $WindowRect.Top, [WinCapNative]::SRCCOPY)
    [void][WinCapNative]::ReleaseDC([IntPtr]::Zero, $screen)
    $graphics.ReleaseHdc($dest)
    $graphics.Dispose()
    return $ok
}

$bitmap = New-WindowBitmap -BitmapWidth $width -BitmapHeight $height
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$dest = $graphics.GetHdc()
$captureMethod = "unknown"

if ($PreferPrintWindow -ne 0) {
    $copied = [WinCapNative]::PrintWindow($window.Handle, $dest, 2)
    $captureMethod = "printwindow"
} else {
    $screen = [WinCapNative]::GetDC([IntPtr]::Zero)
    $copied = [WinCapNative]::BitBlt($dest, 0, 0, $width, $height, $screen, $rect.Left, $rect.Top, [WinCapNative]::SRCCOPY)
    [void][WinCapNative]::ReleaseDC([IntPtr]::Zero, $screen)
    $captureMethod = "bitblt"
}

$graphics.ReleaseHdc($dest)
$graphics.Dispose()

if ($PreferPrintWindow -ne 0 -and (($copied -eq $false) -or (Test-BitmapMostlyBlack -Bitmap $bitmap))) {
    $bitmap.Dispose()
    [void][WinCapNative]::SetForegroundWindow($window.Handle)
    Start-Sleep -Milliseconds 750
    [void][WinCapNative]::GetWindowRect($window.Handle, [ref]$rect)
    $bitmap = New-WindowBitmap -BitmapWidth $width -BitmapHeight $height
    $copied = Copy-WindowFromScreen -TargetBitmap $bitmap -WindowRect $rect
    $captureMethod = "bitblt-after-black-printwindow"
}

if ($PreferPrintWindow -eq 0 -and -not $copied) {
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $hdc = $graphics.GetHdc()
    [void][WinCapNative]::PrintWindow($window.Handle, $hdc, 2)
    $graphics.ReleaseHdc($hdc)
    $graphics.Dispose()
    $captureMethod = "printwindow-fallback"
}

$dir = Split-Path -Parent $OutFile
if ($dir -and -not (Test-Path $dir)) {
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
}
$mostlyBlack = Test-BitmapMostlyBlack -Bitmap $bitmap
$bitmap.Save($OutFile, [System.Drawing.Imaging.ImageFormat]::Png)
$bitmap.Dispose()

[pscustomobject]@{
    OutFile = $OutFile
    WindowTitle = $window.Title
    ProcessId = $window.ProcessId
    Width = $width
    Height = $height
    Left = $rect.Left
    Top = $rect.Top
    Right = $rect.Right
    Bottom = $rect.Bottom
    MoveWindowSucceeded = $moved
    CaptureMethod = $captureMethod
    MostlyBlack = $mostlyBlack
} | ConvertTo-Json -Compress
'''


def require_windows() -> None:
    if os.name != "nt":
        raise SystemExit("This proof-of-concept must be run on Windows 10/11.")


def ensure_output_dir(out_dir: str) -> Path:
    path = Path(out_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def timestamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def write_capture_helper(out_dir: Path) -> Path:
    helper_dir = out_dir / "_work" / "_helpers"
    helper_dir.mkdir(parents=True, exist_ok=True)
    helper = helper_dir / "capture_window.ps1"
    helper.write_text(CAPTURE_PS1, encoding="utf-8")
    return helper


def metadata_is_complete(args: argparse.Namespace) -> bool:
    return bool(args.host and args.port and args.service)


def build_run_id(args: argparse.Namespace, capture_kind: str) -> str:
    unique = timestamp()
    if not metadata_is_complete(args):
        return f"{capture_kind}-{unique}"

    host = sanitize_evidence_part(args.host)
    port = sanitize_evidence_part(args.port)
    service = sanitize_evidence_part(args.service)
    return f"{host}_{port}_{service}_{capture_kind}-{unique}"


def primary_evidence_path(out_dir: Path, args: argparse.Namespace, run_id: str) -> Path:
    filename = f"{run_id}.png"
    if not metadata_is_complete(args):
        return out_dir / filename

    host = sanitize_evidence_part(args.host)
    service = sanitize_evidence_part(args.service)
    return out_dir / "evidence" / "by_host" / host / service / filename


def copy_evidence_paths(primary: Path, out_dir: Path, args: argparse.Namespace) -> list[Path]:
    if not metadata_is_complete(args):
        return [primary]

    host = sanitize_evidence_part(args.host)
    service = sanitize_evidence_part(args.service)
    filename = primary.name
    paths = [
        out_dir / "evidence" / "by_host" / host / service / filename,
        out_dir / "evidence" / "by_service" / service / host / filename,
    ]

    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)

    if primary != paths[0]:
        shutil.copy2(primary, paths[0])
    shutil.copy2(paths[0], paths[1])
    return paths


def run_powershell_capture(
    helper: Path,
    outfile: Path,
    *,
    pid: int = 0,
    process_name: str = "",
    title_contains: str = "",
    timeout_seconds: int = 20,
    prefer_print_window: bool = False,
) -> str:
    ps = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if not ps:
        raise RuntimeError("Could not find powershell.exe or pwsh.exe on PATH.")

    outfile.parent.mkdir(parents=True, exist_ok=True)

    args = [
        ps,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(helper),
        "-OutFile",
        str(outfile),
        "-TimeoutSeconds",
        str(timeout_seconds),
    ]
    if pid:
        args += ["-ProcessId", str(pid)]
    if process_name:
        args += ["-ProcessName", process_name]
    if title_contains:
        args += ["-TitleContains", title_contains]
    if prefer_print_window:
        args += ["-PreferPrintWindow", "1"]

    completed = subprocess.run(args, text=True, capture_output=True)
    if completed.returncode != 0:
        raise RuntimeError(
            "PowerShell capture failed.\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    return completed.stdout.strip()


def find_edge() -> Path:
    candidates = [
        os.environ.get("PROGRAMFILES", r"C:\Program Files") + r"\Microsoft\Edge\Application\msedge.exe",
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)") + r"\Microsoft\Edge\Application\msedge.exe",
        os.environ.get("LOCALAPPDATA", "") + r"\Microsoft\Edge\Application\msedge.exe",
    ]

    on_path = shutil.which("msedge.exe")
    if on_path:
        candidates.insert(0, on_path)

    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return Path(candidate)

    raise RuntimeError("Could not find msedge.exe in common install paths or PATH.")


def close_process_tree(pid: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        text=True,
        capture_output=True,
    )


def close_existing_edge_processes() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["taskkill", "/IM", "msedge.exe", "/T", "/F"],
        text=True,
        capture_output=True,
    )


def capture_result_is_mostly_black(result: str) -> bool:
    if not result:
        return False
    try:
        data = json.loads(result.splitlines()[-1])
    except (json.JSONDecodeError, IndexError, TypeError):
        return False
    return bool(data.get("MostlyBlack"))


def saved_png_is_mostly_black(path: Path) -> bool:
    ps = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if not ps or not path.exists():
        return False

    script = "Add-Type -AssemblyName System.Drawing\n$Bitmap = [System.Drawing.Bitmap]::FromFile($args[0])\ntry {\n    $stepX = [Math]::Max(1, [int]($Bitmap.Width / 40))\n    $stepY = [Math]::Max(1, [int]($Bitmap.Height / 40))\n    $total = 0\n    $dark = 0\n    for ($y = 0; $y -lt $Bitmap.Height; $y += $stepY) {\n        for ($x = 0; $x -lt $Bitmap.Width; $x += $stepX) {\n            $pixel = $Bitmap.GetPixel($x, $y)\n            $brightness = ([int]$pixel.R + [int]$pixel.G + [int]$pixel.B) / 3\n            if (($pixel.A -lt 16) -or ($brightness -lt 12)) { $dark++ }\n            $total++\n        }\n    }\n    if ($total -eq 0) { 'false' }\n    elseif (($dark / $total) -gt 0.97) { 'true' }\n    else { 'false' }\n} finally {\n    $Bitmap.Dispose()\n}\n"
    completed = subprocess.run(
        [ps, "-NoProfile", "-Command", script, str(path)],
        text=True,
        capture_output=True,
    )
    return completed.returncode == 0 and completed.stdout.strip().lower().endswith("true")


def run_edge_headless_capture(edge: Path, args: argparse.Namespace, user_data_dir: Path, outfile: Path) -> str:
    outfile.parent.mkdir(parents=True, exist_ok=True)
    # Use a separate profile for fallback. GUI Edge can leave child/background
    # processes holding the normal profile even after the launcher PID exits,
    # and Chromium then fails headless startup with little or no stderr.
    fallback_data_dir = user_data_dir.parent / "_edge_headless_profile"
    if fallback_data_dir.exists():
        shutil.rmtree(fallback_data_dir, ignore_errors=True)
    seed_edge_profile(fallback_data_dir)
    headless_args = [
        str(edge),
        f"--user-data-dir={fallback_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "--disable-gpu",
        "--disable-features=CalculateNativeWinOcclusion",
        "--ignore-certificate-errors",
        "--allow-insecure-localhost",
        "--disable-session-crashed-bubble",
        "--headless=new",
        f"--screenshot={outfile}",
        f"--window-size={EDGE_SIZE}",
        args.url,
    ]
    completed = subprocess.run(
        headless_args,
        text=True,
        capture_output=True,
        timeout=max(args.capture_timeout, 30),
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Edge headless screenshot fallback failed.\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    if not outfile.exists() or outfile.stat().st_size <= 0:
        raise RuntimeError(f"Edge headless screenshot fallback did not create a non-empty file: {outfile}")
    return json.dumps(
        {
            "OutFile": str(outfile),
            "WindowTitle": "headless-edge-fallback",
            "ProcessId": 0,
            "Width": EDGE_SIZE.split(",")[0],
            "Height": EDGE_SIZE.split(",")[1],
            "CaptureMethod": "edge-headless-screenshot-fallback",
            "MostlyBlack": False,
        },
        separators=(",", ":"),
    )


def sanitize_cmd_title(value: str) -> str:
    safe = "".join("_" if char in '&|<>^"\r\n' else char for char in value)
    return safe.strip() or "Evidence"


def write_json_if_missing(path: Path, data: dict) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def seed_edge_profile(user_data_dir: Path) -> None:
    user_data_dir.mkdir(parents=True, exist_ok=True)
    (user_data_dir / "First Run").touch(exist_ok=True)

    write_json_if_missing(
        user_data_dir / "Local State",
        {
            "browser": {
                "enabled_labs_experiments": [],
            },
            "distribution": {
                "make_chrome_default": False,
                "make_chrome_default_for_user": False,
                "show_welcome_page": False,
                "skip_first_run_ui": True,
                "suppress_first_run_bubble": True,
            },
            "first_run_tabs": [],
            "profile": {
                "info_cache": {},
            },
        },
    )
    write_json_if_missing(
        user_data_dir / "Default" / "Preferences",
        {
            "browser": {
                "check_default_browser": False,
                "has_seen_welcome_page": True,
            },
            "edge": {
                "show_first_run_experience": False,
            },
            "profile": {
                "exited_cleanly": True,
                "exit_type": "Normal",
                "name": "Evidence Capture",
            },
            "signin": {
                "allowed": False,
            },
            "sync": {
                "requested": False,
                "suppress_start": True,
            },
        },
    )


def launch_edge(args: argparse.Namespace) -> None:
    require_windows()
    out_dir = ensure_output_dir(args.out)
    helper = write_capture_helper(out_dir)

    run_id = build_run_id(args, "edge")
    user_data_dir = out_dir / "_work" / "_edge_profile"
    seed_edge_profile(user_data_dir)
    outfile = primary_evidence_path(out_dir, args, run_id)

    edge = find_edge()
    if args.no_pre_clean:
        print("edge_pre_clean_attempted=false")
        print("edge_pre_clean_reason=no-pre-clean")
    else:
        completed = close_existing_edge_processes()
        print("edge_pre_clean_attempted=true")
        print(f"edge_pre_clean_returncode={completed.returncode}")
        if completed.stdout.strip():
            print(f"edge_pre_clean_stdout={completed.stdout.strip()}")
        if completed.stderr.strip():
            print(f"edge_pre_clean_stderr={completed.stderr.strip()}")

    edge_args = [
        str(edge),
        "--new-window",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "--disable-gpu",
        "--disable-features=CalculateNativeWinOcclusion",
        "--ignore-certificate-errors",
        "--allow-insecure-localhost",
        "--disable-session-crashed-bubble",
        f"--window-size={EDGE_SIZE}",
        args.url,
    ]

    proc = subprocess.Popen(edge_args)
    gui_capture_error = ""
    try:
        time.sleep(args.wait)

        try:
            result = run_powershell_capture(
                helper,
                outfile,
                pid=proc.pid,
                process_name="msedge",
                title_contains=args.title_contains,
                timeout_seconds=args.capture_timeout,
                prefer_print_window=True,
            )
            if capture_result_is_mostly_black(result) or saved_png_is_mostly_black(outfile):
                raise RuntimeError("GUI Edge capture was mostly black; falling back to Edge headless screenshot.")
        except RuntimeError as exc:
            gui_capture_error = str(exc)
            completed = close_process_tree(proc.pid)
            print("edge_gui_close_before_fallback_attempted=true")
            print(f"edge_gui_close_before_fallback_returncode={completed.returncode}")
            cleanup = close_existing_edge_processes()
            print("edge_headless_pre_clean_attempted=true")
            print(f"edge_headless_pre_clean_returncode={cleanup.returncode}")
            if cleanup.stdout.strip():
                print(f"edge_headless_pre_clean_stdout={cleanup.stdout.strip()}")
            if cleanup.stderr.strip():
                print(f"edge_headless_pre_clean_stderr={cleanup.stderr.strip()}")
            result = run_edge_headless_capture(edge, args, user_data_dir, outfile)
            proc = None

        evidence_paths = copy_evidence_paths(outfile, out_dir, args)

        print(f"edge_pid={proc.pid if proc is not None else 0}")
        print(f"user_data_dir={user_data_dir}")
        if gui_capture_error:
            print(f"edge_gui_capture_error={gui_capture_error}")
        for index, path in enumerate(evidence_paths, start=1):
            print(f"screenshot_{index}={path}")
        if result:
            print(result)
    finally:
        if proc is None:
            print("edge_close_attempted=false")
            print("edge_close_reason=already-closed-before-fallback")
        elif args.keep_open:
            print("edge_close_attempted=false")
            print("edge_close_reason=keep-open")
        else:
            completed = close_process_tree(proc.pid)
            print("edge_close_attempted=true")
            print(f"edge_close_returncode={completed.returncode}")
            if completed.stdout.strip():
                print(f"edge_close_stdout={completed.stdout.strip()}")
            if completed.stderr.strip():
                print(f"edge_close_stderr={completed.stderr.strip()}")


def launch_cmd(args: argparse.Namespace) -> None:
    require_windows()
    out_dir = ensure_output_dir(args.out)
    helper = write_capture_helper(out_dir)

    run_id = build_run_id(args, "cmd")
    title = f"{sanitize_cmd_title(args.title)} [{run_id}]"
    outfile = primary_evidence_path(out_dir, args, run_id)
    work_dir = out_dir / "_work" if metadata_is_complete(args) else out_dir
    work_dir.mkdir(parents=True, exist_ok=True)
    command_file = work_dir / f"{run_id}.command.txt"
    runner = work_dir / f"{run_id}.cmd"

    command_file.write_text(args.command + "\n", encoding="utf-8")
    runner.write_text(
        "\n".join(
            [
                "@echo off",
                f"title {title}",
                "mode con cols=140 lines=40",
                f"echo Evidence title: {title}",
                f"echo Running {args.service or 'service'} evidence check...",
                "echo.",
                args.command,
                'set "SPIKE_RC=%ERRORLEVEL%"',
                "echo.",
                "echo Exit code: %SPIKE_RC%",
                "echo Screenshot pending. This window may be closed after capture.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    proc = subprocess.Popen(["cmd.exe", "/k", str(runner)], creationflags=creationflags)
    time.sleep(args.wait)

    result = run_powershell_capture(
        helper,
        outfile,
        pid=proc.pid,
        title_contains=run_id,
        timeout_seconds=args.capture_timeout,
    )
    evidence_paths = copy_evidence_paths(outfile, out_dir, args)

    print(f"cmd_pid={proc.pid}")
    print(f"title={title}")
    print(f"runner={runner}")
    print(f"command_file={command_file}")
    for index, path in enumerate(evidence_paths, start=1):
        print(f"screenshot_{index}={path}")
    if result:
        print(result)



SERVICE_ALIASES = {
    "microsoft-ds": "smb",
    "netbios-ssn": "smb",
    "ssl/http": "https",
    "http-alt": "http",
}

BROWSER_SERVICES = {"http", "https"}
DEFAULT_PORT_SERVICES = {
    "22": "ssh",
    "23": "telnet",
    "2323": "telnet",
    "21": "ftp",
    "2121": "ftp",
    "445": "smb",
    "139": "smb",
    "80": "http",
    "8080": "http",
    "443": "https",
    "8443": "https",
}


def script_dir() -> Path:
    return Path(__file__).resolve().parent


def output_root_for(xml_path: Path) -> Path:
    return script_dir() / f"PortProof-{timestamp()}"


def normalize_service(name: str, port: str, tunnel: str = "") -> str:
    raw = (name or "").strip().lower()
    if tunnel.lower() == "ssl" and raw in {"http", "http-alt", "www"}:
        return "https"
    if raw in SERVICE_ALIASES:
        return SERVICE_ALIASES[raw]
    if raw in {"ssh", "telnet", "ftp", "smb", "http", "https"}:
        return raw
    return DEFAULT_PORT_SERVICES.get(str(port), raw or "unknown")


def parse_nmap_xml(xml_path: Path) -> list[dict]:
    import xml.etree.ElementTree as ET

    root = ET.parse(xml_path).getroot()
    targets: list[dict] = []
    for host in root.findall("host"):
        state = host.find("status")
        if state is not None and state.get("state") not in {None, "up"}:
            continue
        address = ""
        for addr in host.findall("address"):
            if addr.get("addrtype") in {"ipv4", "ipv6", None}:
                address = addr.get("addr", "")
                break
        if not address:
            continue
        for port_el in host.findall("./ports/port"):
            state_el = port_el.find("state")
            if state_el is None or state_el.get("state") != "open":
                continue
            port = port_el.get("portid", "")
            proto = port_el.get("protocol", "tcp")
            service_el = port_el.find("service")
            service_name = service_el.get("name", "") if service_el is not None else ""
            tunnel = service_el.get("tunnel", "") if service_el is not None else ""
            product = service_el.get("product", "") if service_el is not None else ""
            version = service_el.get("version", "") if service_el is not None else ""
            normalized = normalize_service(service_name, port, tunnel)
            if normalized in {"ssh", "telnet", "ftp", "smb", "http", "https"}:
                targets.append(
                    {
                        "host": address,
                        "port": port,
                        "protocol": proto,
                        "service": normalized,
                        "nmap_service": service_name,
                        "product": product,
                        "version": version,
                    }
                )
    return targets


def service_url(host: str, port: str, service: str) -> str:
    scheme = "https" if service == "https" else "http"
    return f"{scheme}://{host}:{port}/"


def powershell_encoded(script: str) -> str:
    import base64

    return base64.b64encode(script.encode("utf-16le")).decode("ascii")


def command_for_service(host: str, port: str, service: str, commands_dir: Path) -> str:
    if service == "ssh":
        known_hosts = commands_dir / "ssh_known_hosts"
        return (
            "ssh.exe "
            "-o StrictHostKeyChecking=accept-new "
            f"-o UserKnownHostsFile=\"{known_hosts}\" "
            "-o PreferredAuthentications=password "
            "-o PubkeyAuthentication=no "
            "-o BatchMode=no "
            "-o ConnectionAttempts=1 "
            f"-p {port} portproof@{host}"
        )
    if service == "telnet":
        ps = f"$c=New-Object Net.Sockets.TcpClient; $c.ReceiveTimeout=5000; $c.Connect('{host}',{port}); 'TELNET TCP {port} connected'; $s=$c.GetStream(); $deadline=(Get-Date).AddSeconds(5); while(-not $s.DataAvailable -and (Get-Date) -lt $deadline){{Start-Sleep -Milliseconds 100}}; if($s.DataAvailable){{$b=New-Object byte[] 1024; $n=$s.Read($b,0,$b.Length); [Text.Encoding]::ASCII.GetString($b,0,$n)}} else {{'No login prompt or banner before timeout'}}; Start-Sleep -Seconds 60; $c.Close()"
        return f"powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand {powershell_encoded(ps)}"
    if service == "ftp":
        return f"curl.exe --connect-timeout 10 --max-time 30 --user anonymous:anonymous --list-only ftp://{host}:{port}/"
    if service == "smb":
        ps = rf"""
Write-Output 'SMB share listing:'
$netViewTimeoutSeconds = 8
$netViewJob = Start-Job -ScriptBlock {{
    param($TargetHost)
    $output = @(& $env:ComSpec /d /c "net view \\$TargetHost 2>&1")
    [pscustomobject]@{{ Output = $output; ExitCode = $LASTEXITCODE }}
}} -ArgumentList '{host}'
$netViewCompleted = Wait-Job -Job $netViewJob -Timeout $netViewTimeoutSeconds
$netView = @()
$netViewExit = $null
if ($netViewCompleted) {{
    $netViewResult = Receive-Job -Job $netViewJob
    $netView = @($netViewResult.Output)
    $netViewExit = $netViewResult.ExitCode
    Remove-Job -Job $netViewJob -Force | Out-Null
    if ($netViewExit -ne 0) {{
        Write-Output ('net view failed with exit code ' + $netViewExit + '.')
        $netView | Select-Object -First 6 | ForEach-Object {{ Write-Output $_ }}
    }} else {{
        $netView | ForEach-Object {{ Write-Output $_ }}
    }}
}} else {{
    Stop-Job -Job $netViewJob -ErrorAction SilentlyContinue | Out-Null
    Remove-Job -Job $netViewJob -Force -ErrorAction SilentlyContinue | Out-Null
    Write-Output ('net view timed out after ' + $netViewTimeoutSeconds + ' seconds.')
}}
$share = $null
foreach ($line in $netView) {{
    if ($line -match '^\s*(\S+)\s+Disk\s+') {{ $share = $Matches[1]; break }}
}}
if ($share) {{
    Write-Output ''
    $unc = '\\{host}\' + $share
    Write-Output ('SMB file listing: ' + $unc)
    try {{
        $items = Get-ChildItem -LiteralPath $unc -Force -ErrorAction Stop
        if ($items) {{
            $items | Select-Object Mode, LastWriteTime, Length, Name | Format-Table -AutoSize | Out-String -Width 200 | ForEach-Object {{ Write-Output $_ }}
        }} else {{
            Write-Output '(share is accessible but empty)'
        }}
    }} catch {{
        Write-Output ('SMB file listing failed: ' + $_.Exception.Message)
    }}
}} else {{
    Write-Output ''
    Write-Output 'No disk share was listed, so file listing could not be captured.'
}}
""".strip()
        return f"powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand {powershell_encoded(ps)}"
    ps = f"$r=Test-NetConnection -ComputerName {host} -Port {port} -InformationLevel Detailed; $r | Out-String"
    return f"powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand {powershell_encoded(ps)}"


def append_log(log_path: Path, heading: str, content: str) -> None:
    with log_path.open("a", encoding="utf-8", errors="replace") as f:
        f.write(f"\n===== {heading} =====\n")
        f.write(content.rstrip() + "\n")


def run_capture_subcommand(argv: list[str]) -> tuple[int, str]:
    parser = build_internal_parser()
    args = parser.parse_args(argv)
    from io import StringIO
    import contextlib

    stdout = StringIO()
    stderr = StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        rc = 0
        try:
            args.func(args)
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            rc = 1
    combined = stdout.getvalue()
    err = stderr.getvalue()
    if err:
        combined += ("\n[stderr]\n" + err)
    return rc, combined


def extract_screenshot_path(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("screenshot_1="):
            return line.split("=", 1)[1].strip()
    return ""


def write_csv(path: Path, rows: list[dict]) -> None:
    import csv

    fields = [
        "host",
        "port",
        "protocol",
        "service",
        "nmap_service",
        "product",
        "version",
        "status",
        "capture_method",
        "screenshot",
        "notes",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def write_xlsx(path: Path, rows: list[dict]) -> None:
    import zipfile
    from html import escape

    fields = ["host", "port", "protocol", "service", "nmap_service", "product", "version", "status", "capture_method", "screenshot", "notes"]
    labels = ["Host", "Port", "Protocol", "Service", "Nmap Service", "Product", "Version", "Status", "Capture Method", "Screenshot", "Notes"]

    def cell(value: str) -> str:
        return f'<c t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'

    sheet_rows = ["<row>" + "".join(cell(v) for v in labels) + "</row>"]
    for row in rows:
        sheet_rows.append("<row>" + "".join(cell(row.get(field, "")) for field in fields) + "</row>")
    sheet = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>%s</sheetData></worksheet>""" % "".join(sheet_rows)
    workbook = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="PortProof" sheetId="1" r:id="rId1"/></sheets></workbook>"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>"""
    wb_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>"""
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet)


def cleanup_runtime_dirs(out_dir: Path) -> None:
    work = out_dir / "_work"
    for name in ("_edge_profile", "_edge_headless_profile", "_helpers"):
        shutil.rmtree(work / name, ignore_errors=True)


def move_command_artifacts(out_dir: Path, commands_dir: Path) -> None:
    work = out_dir / "_work"
    if not work.exists():
        return
    commands_dir.mkdir(parents=True, exist_ok=True)
    for pattern in ("*.cmd", "*.command.txt"):
        for src in work.glob(pattern):
            dst = commands_dir / src.name
            if dst.exists():
                dst.unlink()
            shutil.move(str(src), str(dst))


def run_portproof(nmap_xml: Path) -> int:
    nmap_xml = nmap_xml.expanduser().resolve()
    if not nmap_xml.exists():
        print(f"Nmap XML file not found: {nmap_xml}", file=sys.stderr)
        return 2

    out_dir = output_root_for(nmap_xml)
    evidence_dir = out_dir / "evidence"
    commands_dir = out_dir / "command-artifacts"
    logs_dir = out_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    commands_dir.mkdir(parents=True, exist_ok=True)
    consolidated_log = logs_dir / "portproof-run-log.txt"
    consolidated_log.write_text(f"PortProof run: {timestamp()}\nNmap XML: {nmap_xml}\nOutput: {out_dir}\n", encoding="utf-8")
    shutil.copy2(nmap_xml, out_dir / nmap_xml.name)

    targets = parse_nmap_xml(nmap_xml)
    rows: list[dict] = []
    append_log(consolidated_log, "parsed-targets", json.dumps(targets, indent=2))

    for target in targets:
        host = target["host"]
        port = target["port"]
        service = target["service"]
        base = {
            **target,
            "status": "pending",
            "capture_method": "",
            "screenshot": "",
            "notes": "",
        }
        if service in BROWSER_SERVICES:
            argv = [
                "edge",
                "--url",
                service_url(host, port, service),
                "--out",
                str(out_dir),
                "--host",
                host,
                "--port",
                port,
                "--service",
                service,
                "--wait",
                "12",
                "--capture-timeout",
                "30",
            ]
        else:
            title = f"{service}-{port}"
            argv = [
                "cmd",
                "--title",
                title,
                "--command",
                command_for_service(host, port, service, commands_dir),
                "--out",
                str(out_dir),
                "--host",
                host,
                "--port",
                port,
                "--service",
                service,
                "--wait",
                "8" if service != "smb" else "15",
                "--capture-timeout",
                "25",
            ]
        rc, output = run_capture_subcommand(argv)
        append_log(consolidated_log, f"{host}:{port}/{service}", output)
        shot = extract_screenshot_path(output)
        base["status"] = "captured" if rc == 0 and shot else "failed"
        base["screenshot"] = str(Path(shot).relative_to(out_dir)) if shot else ""
        for line in output.splitlines():
            if line.startswith("{") and "CaptureMethod" in line:
                try:
                    base["capture_method"] = json.loads(line).get("CaptureMethod", "")
                except Exception:
                    pass
        if rc != 0:
            base["notes"] = "capture command failed; see logs/portproof-run-log.txt"
        rows.append(base)
        move_command_artifacts(out_dir, commands_dir)
        cleanup_runtime_dirs(out_dir)

    write_csv(out_dir / "portproof-results.csv", rows)
    write_xlsx(out_dir / "portproof-results.xlsx", rows)
    cleanup_runtime_dirs(out_dir)
    move_command_artifacts(out_dir, commands_dir)

    print(f"output_dir={out_dir}")
    print(f"csv={out_dir / 'portproof-results.csv'}")
    print(f"xlsx={out_dir / 'portproof-results.xlsx'}")
    print(f"log={consolidated_log}")
    print(f"evidence_dir={evidence_dir}")
    return 0 if all(row["status"] == "captured" for row in rows) else 1


def build_internal_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    def add_metadata_args(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--host")
        subparser.add_argument("--port")
        subparser.add_argument("--service")

    edge_parser = subparsers.add_parser("edge")
    add_metadata_args(edge_parser)
    edge_parser.add_argument("--url", required=True)
    edge_parser.add_argument("--out", required=True)
    edge_parser.add_argument("--wait", type=float, default=DEFAULT_EDGE_WAIT_SECONDS)
    edge_parser.add_argument("--capture-timeout", type=int, default=20)
    edge_parser.add_argument("--keep-open", action="store_true")
    edge_parser.add_argument("--no-pre-clean", action="store_true")
    edge_parser.add_argument("--title-contains", default="")
    edge_parser.set_defaults(func=launch_edge)

    cmd_parser = subparsers.add_parser("cmd")
    add_metadata_args(cmd_parser)
    cmd_parser.add_argument("--title", required=True)
    cmd_parser.add_argument("--command", required=True)
    cmd_parser.add_argument("--out", required=True)
    cmd_parser.add_argument("--wait", type=float, default=DEFAULT_WAIT_SECONDS)
    cmd_parser.add_argument("--capture-timeout", type=int, default=20)
    cmd_parser.set_defaults(func=launch_cmd)
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="PortProof.py",
        description="Create port evidence screenshots and CSV/Excel reports from an Nmap XML file.",
    )
    parser.add_argument("nmap_xml", help="Nmap XML file to process.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_portproof(Path(args.nmap_xml))


if __name__ == "__main__":
    raise SystemExit(main())
