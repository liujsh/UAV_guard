param([string]$Python = '.venv/Scripts/python.exe')

$ErrorActionPreference = 'Stop'
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    & $Python -m PyInstaller --noconfirm --windowed --onedir --name UAVGuardWorkbench `
        --paths . --specpath build --workpath build/pyinstaller --distpath dist `
        packaging/windows_entry.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }
} finally {
    Pop-Location
}
