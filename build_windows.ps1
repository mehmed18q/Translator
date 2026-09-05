param(
    [string]$VenvPath = ".venv-build"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$VenvPython = Join-Path $VenvPath "Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        py -3.12 -m venv $VenvPath
    }
    else {
        python -m venv $VenvPath
    }
}

& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r requirements-build.txt
& $VenvPython -m unittest discover -s tests -p "test*.py"
& $VenvPython -m PyInstaller --noconfirm --clean Translator.spec

$ExePath = Join-Path $ProjectRoot "dist\Translator.exe"
if (-not (Test-Path $ExePath)) {
    throw "Build finished without creating $ExePath"
}

$Exe = Get-Item $ExePath
Write-Host "Created $($Exe.FullName) ($([math]::Round($Exe.Length / 1MB, 1)) MB)"
