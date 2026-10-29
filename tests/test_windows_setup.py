"""Exercise installer Python discovery through Windows PowerShell's native pipe."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell 5.1 regression")


@pytest.mark.parametrize("pipe_encoding", ["cp1251", "utf-8"])
@pytest.mark.parametrize("discovery", ["launcher", "path"])
def test_python_discovery_preserves_cyrillic_path(tmp_path, pipe_encoding, discovery):
    powershell = shutil.which("powershell.exe")
    assert powershell, "Windows PowerShell is required to test the Windows installer"
    assert sys.version_info[:2] == (3, 12), "The Windows installer requires Python 3.12"
    source_venv = tmp_path / "Пользователь с пробелами" / "Python312"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(source_venv)],
        check=True, capture_output=True, timeout=60,
    )
    expected_python = source_venv / "Scripts" / "python.exe"
    target_venv = tmp_path / "Новая папка" / ".venv"
    env = {
        **os.environ,
        "PYTHONIOENCODING": pipe_encoding,
        "PYTHONUTF8": "0",
        "KADROGEN_TEST_SETUP": str(Path(__file__).resolve().parents[1] / "scripts" / "setup_windows.ps1"),
        "KADROGEN_TEST_PYTHON": str(expected_python),
        "KADROGEN_TEST_TARGET": str(target_venv),
        "KADROGEN_TEST_DISCOVERY": discovery,
        "LOCALAPPDATA": str(tmp_path / "unused-localappdata"),
    }
    script = r"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Console]::OutputEncoding = [Text.Encoding]::GetEncoding(866)
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:KADROGEN_TEST_SETUP, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
# Load only function definitions: do not run package installation or change PATH
# through the installer's top-level code.
foreach ($statement in $ast.EndBlock.Statements) {
    if ($statement -is [System.Management.Automation.Language.FunctionDefinitionAst]) {
        . ([scriptblock]::Create($statement.Extent.Text))
    }
}
$env:Path = Split-Path -Parent $env:KADROGEN_TEST_PYTHON
if ($env:KADROGEN_TEST_DISCOVERY -eq 'launcher') {
    function py {
        if ($args[0] -ne '-3.12') { throw 'Python launcher version argument missing' }
        $pythonArgs = @($args | Select-Object -Skip 1)
        & $env:KADROGEN_TEST_PYTHON @pythonArgs
        $global:LASTEXITCODE = $LASTEXITCODE
    }
}
$found = Find-Python312
if ($found -cne $env:KADROGEN_TEST_PYTHON) {
    throw 'Python discovery did not preserve the exact Unicode executable path'
}
# Reproduce the first real installation step that previously failed.
& $found -m venv --without-pip $env:KADROGEN_TEST_TARGET
if ($LASTEXITCODE -ne 0) { throw 'Discovered Python could not create a virtual environment' }
Write-Output 'UNICODE_DISCOVERY_OK'
"""
    result = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        env=env, capture_output=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout.decode("cp866", errors="replace") + result.stderr.decode("cp866", errors="replace")
    assert b"UNICODE_DISCOVERY_OK" in result.stdout
    created_python = target_venv / "Scripts" / "python.exe"
    result = subprocess.run(
        [str(created_python), "-c", "import sys; assert sys.version_info[:2] == (3, 12)"],
        capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
