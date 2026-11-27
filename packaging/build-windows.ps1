param([string]$Python = '.venv/Scripts/python.exe')

$ErrorActionPreference = 'Stop'
$previousPath = $env:Path
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    # Keep external Python distributions from supplying DLLs during dependency discovery.
    $env:Path = "$env:SystemRoot\System32;$env:SystemRoot"
    # Use the C++ runtime shipped with the Qt wheel, including on hosts with an older system runtime.
    $qtDirectoryJson = & $Python -c 'import json, PySide6; print(json.dumps(PySide6.__path__[0]))'
    if ($LASTEXITCODE -ne 0) { throw 'Unable to locate the PySide6 runtime' }
    $qtDirectory = ConvertFrom-Json -InputObject $qtDirectoryJson
    $runtime = @()
    foreach ($file in (Get-ChildItem -LiteralPath $qtDirectory -Filter 'msvcp140*.dll')) {
        $runtime += '--add-binary', "$($file.FullName):."
    }
    if ($runtime.Count -eq 0) { throw 'PySide6 runtime DLLs are missing' }
    & $Python -m PyInstaller --noconfirm --windowed --onedir --name UAVGuardWorkbench `
        --paths . --specpath build --workpath build/pyinstaller-isolated --distpath dist @runtime `
        --add-data 'guard_workbench/policies;guard_workbench/policies' packaging/windows_entry.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }
} finally {
    $env:Path = $previousPath
    Pop-Location
}
