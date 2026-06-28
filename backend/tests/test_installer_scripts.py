import os
import shutil
import stat
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _fake_command(path: Path, body: str = "exit 0") -> None:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _installer_workspace(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    workspace = tmp_path / "workspace"
    (workspace / "backend").mkdir(parents=True)
    (workspace / "frontend").mkdir()
    shutil.copy2(ROOT / "install.sh", workspace / "install.sh")
    shutil.copy2(ROOT / "start.sh", workspace / "start.sh")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_command(
        fake_bin / "node",
        'if [[ "$1" == "-p" ]]; then printf "22.12.0\\n"; else printf "v22.12.0\\n"; fi',
    )
    _fake_command(fake_bin / "npm")
    _fake_command(fake_bin / "uv")

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env.pop("WSL_DISTRO_NAME", None)
    return workspace, env


def _run_installer(workspace: Path, env: dict[str, str], answers: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "install.sh"],
        cwd=workspace,
        env=env,
        input="\n".join(answers) + "\n",
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )


def test_bash_installer_first_run_reaches_completion(tmp_path: Path):
    workspace, env = _installer_workspace(tmp_path)

    result = _run_installer(
        workspace,
        env,
        ["127.0.0.1", "8080", "Asia/Taipei", "", "", "", "", "n"],
    )

    assert result.returncode == 0, result.stderr
    assert "Installation complete" in result.stdout
    assert (workspace / "backend/.env").exists()
    assert (workspace / ".vocafsrs.conf").read_text(encoding="utf-8") == "HOST=0.0.0.0\nPORT=8080\n"


def test_bash_installer_keeps_secrets_but_updates_public_url(tmp_path: Path):
    workspace, env = _installer_workspace(tmp_path)
    env_file = workspace / "backend/.env"
    env_file.write_text(
        "\n".join(
            [
                'OPENROUTER_API_KEY="keep-me"',
                'ALLOWED_ORIGINS="http://192.0.2.10:8080"',
                'OPENROUTER_SITE_URL="http://192.0.2.10:8080"',
                'APP_PUBLIC_URL="http://192.0.2.10:8080"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = _run_installer(
        workspace,
        env,
        ["192.0.2.20", "9090", "n", "", "n"],
    )

    assert result.returncode == 0, result.stderr
    content = env_file.read_text(encoding="utf-8")
    assert 'OPENROUTER_API_KEY="keep-me"' in content
    assert 'APP_PUBLIC_URL="http://192.0.2.20:9090"' in content
    assert 'ALLOWED_ORIGINS="http://192.0.2.20:9090"' in content
    assert (workspace / ".vocafsrs.conf").read_text(encoding="utf-8") == "HOST=0.0.0.0\nPORT=9090\n"
