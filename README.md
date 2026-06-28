# VocaFSRS

VocaFSRS is a self-hosted English vocabulary review app for Chinese-speaking learners. It combines free-text recall, large language model (LLM) grading, and Free Spaced Repetition Scheduler (FSRS) scheduling.

The workflow has two stages:

1. Triage the imported terms as known, unsure, or unknown
2. Review selected terms with typed answers while FSRS schedules later reviews

## How it runs

VocaFSRS requires a server. It is not a static HTML page that runs by itself in a browser.

After installation, one server process provides both the web interface and backend API. SQLite stores the vocabulary and review progress. Keep the server running while using VocaFSRS from the host computer or another device on the same network.

VocaFSRS is designed for one user on a trusted local network. It does not include user accounts or application-wide authentication.

## Requirements

- Git, when cloning or updating the repository
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Node.js 20.19+, excluding 21.x, or Node.js 22.12+
- npm
- An API key for a supported LLM service when using graded review

## Install

Clone or download the repository, then choose the command for the host operating system.

### Windows PowerShell

Open the repository directory in PowerShell and run:

```powershell
.\install.ps1
```

If the PowerShell execution policy blocks local scripts, run:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

### Linux, macOS, and WSL

Open the repository directory in a Bash terminal and run:

```bash
./install.sh
```

If the repository was downloaded as an archive and the executable permission was lost, run `bash install.sh` instead.

When using WSL, install Git, uv, Node.js, and npm inside the WSL distribution. Store the repository in the WSL filesystem and use `install.sh`, not `install.ps1`.

If WSL is not installed, run `wsl --install` from an administrator PowerShell window and restart Windows.

The interactive installer:

- checks the required tools
- asks for the host address and web port
- creates the backend configuration
- installs backend and frontend dependencies
- builds the web interface
- creates or upgrades the SQLite database
- optionally imports a vocabulary file
- optionally configures Discord review reminders

The host address, timezone, vocabulary file, and reminder settings can be changed later. Formal review grading requires an LLM API key. Local settings and credentials are stored in `backend/.env`; restart the server after changing that file.

## Start the server

Use the command for the host operating system.

Windows PowerShell:

```powershell
.\start.ps1
```

Linux, macOS, or WSL:

```bash
./start.sh
```

The terminal displays the server address. Keep that terminal open and press `Ctrl+C` to stop VocaFSRS.

Run the appropriate start command again after restarting the host computer.

## Open VocaFSRS from another device

Open the address shown during installation from a phone, tablet, or another computer on the same network.

On the host computer, open `http://localhost:8080` and replace `8080` if a different port was selected.

For example:

```text
http://192.0.2.10:8080
```

This is a documentation example. Use the local network address of the computer running VocaFSRS.

## Troubleshoot Windows and WSL connections

Find the Windows local network address in PowerShell:

```powershell
Get-NetIPAddress -AddressFamily IPv4 |
  Where-Object {
    $_.IPAddress -notlike "169.254*" -and
    $_.IPAddress -ne "127.0.0.1"
  } |
  Select-Object IPAddress, InterfaceAlias
```

If Windows blocks the selected port, allow it from an administrator PowerShell window:

```powershell
$Port = 8080
New-NetFirewallRule -DisplayName "VocaFSRS" `
  -Direction Inbound -Protocol TCP `
  -LocalPort $Port -Action Allow
```

If another device still cannot connect, follow [Microsoft's WSL networking guide](https://learn.microsoft.com/windows/wsl/networking). WSL mirrored networking is the preferred option on supported Windows versions.

## Import a vocabulary file

The source file can stay anywhere the server can read. Enter its path when the installer asks, or import it later through the web interface. VocaFSRS stores imported entries in SQLite, so the source file does not need to remain inside the repository.

Imports support UTF-8 `.txt` and `.csv` files. Each entry contains:

- **English term**: the prompt shown during study
- **Chinese meaning**: the expected answer

### TXT format

Put one entry on each line and separate the fields with a tab:

```text
abandon	放棄；拋棄
a board member	董事會成員
```

Two or more spaces also work, but a tab is safer when a term or meaning contains spaces.

### CSV format

Use `term` and `meaning` headers:

```csv
term,meaning
abandon,放棄；拋棄
a board member,董事會成員
```

See [sample-vocabulary.txt](docs/sample-vocabulary.txt) and [sample-vocabulary.csv](docs/sample-vocabulary.csv) for importable templates.

CSV imports can also include `part_of_speech`, `sense_hint`, `example_sentence`, and `example_translation`. These optional fields help distinguish multiple meanings of the same English term.

## Study workflow

### Triage

Triage classifies the entire imported list:

- **Known**
- **Unsure**
- **Unknown**

Complete triage before starting formal review. You can stop and resume without losing the current progress.

### FSRS review

Formal review uses typed answers instead of multiple-choice options. At the end of the session, VocaFSRS sends the answers for one batch grading request.

Each answer receives one of three ratings:

- `Again`
- `Hard`
- `Good`

FSRS combines the rating with the term's review history to calculate its next due time.

The mistakes page can export the current session's difficult terms for later review or listening practice.

Without an LLM API key, vocabulary import and triage still work. Graded review sessions cannot finish until a key is configured.

## Maintain the installation

Review progress is stored in `backend/data/vocab.db`. Stop the server and copy `vocab.db` plus any matching `-wal` and `-shm` files before updating or restoring data.

To update, run `git pull`, then rerun the installer for the host operating system. Keep the existing `backend/.env` when prompted unless the configuration needs to be replaced.
