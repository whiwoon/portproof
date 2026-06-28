# PortProof

PortProof turns an Nmap XML scan or an existing PortProof report into a small evidence package for Windows lab verification.

It can start from one Nmap XML file, or resume from a previous `portproof-results.csv` / `portproof-results.xlsx`. It launches the appropriate Windows evidence capture for common services, and writes/updates:

- `portproof-results.csv`
- `portproof-results.xlsx`
- `logs/portproof-run-log.txt`
- `evidence/by_host/<host>/*.png`
- `evidence/by_service/<service>/*.png`
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
python .\PortProof.py .\scan.xml --ip 192.168.16.136
python .\PortProof.py .\scan.xml --port 22 --port 445
python .\PortProof.py .\scan.xml --service ssh,smb
```

Filters can be combined. When combined, a row must match every filter category that was provided. For example, `--ip 192.168.16.136 --service ssh --port 22` captures only SSH on `192.168.16.136:22`.

On XML input, PortProof parses the XML first and immediately creates `portproof-results.csv` and `portproof-results.xlsx` with pending rows. After each evidence capture, it updates both report files, so an interrupted run still records progress.

To resume, pass either report file back in:

```powershell
python .\PortProof.py .\PortProof-20260624-140000\portproof-results.csv
python .\PortProof.py .\PortProof-20260624-140000\portproof-results.xlsx
```

On CSV/XLSX input, PortProof uses the report's parent folder as the output folder. Rows whose `screenshot` value points to an existing file and whose status is `captured` are skipped; missing or failed rows are captured again and the report is updated after each capture. If filters are provided with CSV/XLSX input, the full report is preserved but only matching rows are considered for capture/resume.

```text
PortProof-YYYYMMDD-HHMMSS\
```

## Supported services

PortProof currently captures these open services from Nmap XML:

- SSH (`ssh`, port 22): opens `ssh.exe` as `root@host` and captures the interactive `password:` prompt screen. The capture is intended to show the password prompt, not a TCP banner.
- Telnet (`telnet`, ports 23/2323): opens a TCP console session and captures the login prompt/banner when the service provides one.
- FTP (`ftp`, ports 21/2121): attempts an anonymous FTP directory listing and captures only when a visible file list is returned. Denied or unavailable anonymous listing is recorded as skipped without a screenshot.
- SMB (`microsoft-ds`, `netbios-ssn`, ports 445/139): runs `net view \\host` and then attempts a PowerShell `Get-ChildItem` listing on the first listed disk share. PortProof captures only when a visible file list is returned; denied, empty, timed-out, or unavailable listings are recorded as skipped without a screenshot.
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
    *.ps1
  evidence/
    by_host/<host>/*.png
    by_service/<service>/*.png
```

`_edge_profile`, `_edge_headless_profile`, and `_helpers` are runtime-only directories and are deleted after each capture/run.

## Notes

- File and folder identifiers use timestamps, not random suffixes.
- Console evidence windows are restored, moved to a predictable position, resized before capture, and the capture JSON records the final rectangle plus whether `MoveWindow` succeeded.
- HTTP/HTTPS uses Edge. If GUI capture is black in VMware or remote sessions, PortProof falls back to Edge headless screenshots.
- CSV uses UTF-8 with BOM for easier Excel opening.
- The main terminal prints Korean progress messages such as `[current/total] 캡처 시작 ...`, `캡처됨`, `건너뜀`, or `실패`.
- The main terminal and evidence windows use Korean operator messages. Generated `.cmd` runners switch to UTF-8 code page (`chcp 65001`) and PowerShell helpers are written with UTF-8 BOM plus UTF-8 output encoding so Korean text does not break in Windows console captures.
- Console capture windows are closed automatically after capture or skip so evidence runs do not leave many `cmd.exe` windows behind.
- XLSX is generated with Python standard-library ZIP/XML code, so no `openpyxl` dependency is needed.
- Commands are intentionally simple and suited for lab proof, not credentialed enumeration.
