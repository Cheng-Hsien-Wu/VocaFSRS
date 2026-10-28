# VocaFSRS

VocaFSRS is a self-hosted English vocabulary review app for Chinese-speaking learners. It combines free-text recall, automated semantic grading, and Free Spaced Repetition Scheduler (FSRS) scheduling. Grading supports the D3 Decision API and optional LLM providers.

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
- An API key for D3 or another supported grading service when using graded review

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

At the vocabulary import prompt, enter either an absolute path or a path relative to the directory where you started the installer. For example, if `my-vocabulary.txt` is in the repository directory, enter `my-vocabulary.txt`. The file does not need to be placed in `backend/`. VocaFSRS reads the source file without moving it and stores the imported vocabulary in `backend/data/vocab.db`.

The host address, timezone, vocabulary file, and reminder settings can be changed later. Formal review grading requires an API key. The installer accepts a D3 key first; leaving it blank offers the existing LLM providers. Local environment settings are stored in `backend/.env`; do not share that file. Restart the server after changing it. Settings saved in the web interface take precedence over environment defaults.

In homepage grading settings, select **Decision Model** or **LLM**, then choose the service and model. Decision models currently support **vLLM-SR / D3-turbo** and **Cloudflare Workers AI / CLEF**, sharing the same semantic grading rules. They process one term per request and never fall back to an LLM or another model. Enter the API key under connection settings, then save/test. Cloudflare also requires a 32-character Account ID.

The D3 provider currently serves the `d3-turbo` selector through `d3-ultra`, as documented in its [model list](https://decision.vllm-sr.ai/docs#models). This is a provider-side alias, not our fallback mechanism. Grading records store the actual returned model and revision; unrelated models and inconsistent metadata are rejected.

Environment-based installations can configure one of these providers in `backend/.env`:

| Provider | Settings | Optional Backup Key |
| --- | --- | --- |
| vLLM-SR | `LLM_PROVIDER=d3`, `D3_API_KEY` | `D3_BACKUP_API_KEY` |
| Cloudflare | `LLM_PROVIDER=cloudflare`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN` | `CLOUDFLARE_BACKUP_API_TOKEN` |

Backup keys are configured only in `.env`. If the primary request fails, the backend tries the backup once with the same model and grading instructions. Invalid grading responses are rejected, not retried with another key. Restart after changing environment settings. Each service receives the term, reference meaning, part of speech, and typed answer. Review its data policy before use: [vLLM-SR](https://decision.vllm-sr.ai/privacy) or [Cloudflare Workers AI](https://developers.cloudflare.com/workers-ai/platform/data-usage/). CLEF does not return the same revision metadata as D3, so its stored provenance identifies the model and our grading-policy version, not an immutable provider revision.

Discord reminders are sent only while the VocaFSRS server is running.

## Start the server

Use the command for the host operating system.

Windows PowerShell:

```powershell
.\start.ps1
```

If the execution policy blocks the script, use `powershell -ExecutionPolicy Bypass -File .\start.ps1`.

Linux, macOS, or WSL:

```bash
./start.sh
```

For an archive download without executable permissions, use `bash start.sh`.

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

CSV imports can also include `part_of_speech`, `sense_hint`, `example_sentence`, and `example_translation`. When the same English term and part of speech have different meanings, `sense_hint` distinguishes them; unresolved ambiguous entries are excluded from study.

## Study workflow

### Triage

Triage classifies the entire imported list:

- **Known**
- **Unsure**
- **Unknown**

Complete triage before starting formal review. You can stop and resume without losing the current progress.

After each 100 triage answers, VocaFSRS checks a sample of terms marked **Known**. Complete this checkpoint before continuing. Formal review unlocks only after the full imported list and its checkpoints are complete.

### FSRS review

Formal review uses typed answers instead of multiple-choice options. This makes it harder to get credit by guessing and reduces reliance on learners' own difficulty ratings. Answers are graded after the session; the backend handles individual D3 requests or LLM batches. Automated grading can still make mistakes.

Feedback distinguishes correct, partial, and incorrect meanings. For newly graded answers, scheduling uses:

- `Good`: a complete acceptable core meaning was recalled.
- `Again`: the answer was incomplete or incorrect.

FSRS combines the rating with the term's review history to calculate its next due time. Semantic partial credit is not FSRS `Hard`, which represents successful but effortful recall. Existing historical ratings, including `Hard`, remain unchanged.

The mistakes page can export recent `Again` and `Hard` terms for a NotebookLM podcast or other listening practice.

Without a grading API key, vocabulary import and triage still work. Do not start formal review until a key is configured: answers can be submitted, but grading will fail and another review round remains blocked until the failed grading is resolved.

## Maintain the installation

Review progress is stored in `backend/data/vocab.db`. Stop the server and copy `vocab.db` plus any matching `-wal` and `-shm` files before updating or restoring data.

To update, run `git pull`, then rerun the installer for the host operating system. Keep the existing `backend/.env` when prompted unless the configuration needs to be replaced.
