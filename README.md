# PortProof

PortProof turns an Nmap XML scan into a small evidence package for Windows lab verification.

It reads open ports from one Nmap XML file, launches the appropriate Windows evidence capture for common services, and writes:

- `portproof-results.csv`
- `portproof-results.xlsx`
- `logs/portproof-run-log.txt`
- `evidence/by_host/.../*.png`
- `evidence/by_service/.../*.png`
- `command-artifacts/` for generated `.cmd` and `.command.txt` files

Temporary browser/helper folders are removed after capture.

## Requirements

- Windows 10/11
- Python 3
- Built-in Windows PowerShell
- Microsoft Edge for HTTP/HTTPS screenshots
- An active desktop session for GUI/console captures

No pip packages are required.

## Usage

```powershell
python .\PortProof.py .\scan.xml
```

That is the only public CLI input: one Nmap XML file.

The result folder is created next to `PortProof.py`:

```text
PortProof-YYYYMMDD-HHMMSS\
```

## Supported services

PortProof currently captures these open services from Nmap XML:

- SSH (`ssh`, port 22): opens `ssh.exe` and captures the interactive authentication prompt screen. A disposable username (`portproof`) is used so the evidence shows the login/password prompt instead of only a TCP banner.
- Telnet (`telnet`, ports 23/2323): opens a TCP console session and captures the login prompt/banner when the service provides one.
- FTP (`ftp`, ports 21/2121): runs an anonymous FTP directory listing with `curl.exe --list-only` and captures the visible file list output.
- SMB (`microsoft-ds`, `netbios-ssn`, ports 445/139): runs `net view \\host` to capture the share list, then attempts `dir \\host\share` on the first listed disk share to capture a file listing. If the host does not allow anonymous/guest listing, the denial/error screen is captured and logged.
- HTTP (`http`, ports 80/8080): Microsoft Edge screenshot.
- HTTPS (`ssl/http`, `https`, ports 443/8443): Microsoft Edge screenshot with certificate errors ignored for lab capture.

Unsupported services are ignored for now.

## Output layout

```text
PortProof-YYYYMMDD-HHMMSS/
  portproof-results.csv
  portproof-results.xlsx
  logs/
    portproof-run-log.txt
  command-artifacts/
    *.cmd
    *.command.txt
  evidence/
    by_host/<host>/<service>/*.png
    by_service/<service>/<host>/*.png
```

`_edge_profile`, `_edge_headless_profile`, and `_helpers` are runtime-only directories and are deleted after each capture/run.

## Notes

- File and folder identifiers use timestamps, not random suffixes.
- Console evidence windows are restored, moved to a predictable position, resized before capture, and the capture JSON records the final rectangle plus whether `MoveWindow` succeeded.
- HTTP/HTTPS uses Edge. If GUI capture is black in VMware or remote sessions, PortProof falls back to Edge headless screenshots.
- CSV uses UTF-8 with BOM for easier Excel opening.
- XLSX is generated with Python standard-library ZIP/XML code, so no `openpyxl` dependency is needed.
- Commands are intentionally simple and suited for lab proof, not credentialed enumeration.
