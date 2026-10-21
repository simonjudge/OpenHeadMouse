[CmdletBinding()]
param(
    [ValidatePattern('^\d+\.\d+\.\d+(\.\d+)?$')]
    [string]$Version = "0.2.0",
    [string]$Python = "",
    [string]$Iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
)

$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "This script builds Windows executables and installers. Run it on Windows."
}

if (-not $Python) {
    $venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        $Python = $venvPython
    } else {
        $Python = "python"
    }
}

$pythonCommand = Get-Command $Python -ErrorAction Stop
$compilerCommand = Get-Command $Iscc -ErrorAction Stop

Push-Location $PSScriptRoot
try {
    & $pythonCommand -c "import PyInstaller"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller is missing. Install requirements.txt and pyinstaller into the selected Python environment."
    }

    foreach ($asset in @(
        "config.json",
        "models\face_landmarker.task",
        "models\canonical_face_model.obj"
    )) {
        if (-not (Test-Path -LiteralPath $asset -PathType Leaf)) {
            throw "Required build asset is missing: $asset"
        }
    }

    $distDir = Join-Path $PSScriptRoot "dist"
    $buildDir = Join-Path $PSScriptRoot "build\local-release"
    $specDir = Join-Path $buildDir "specs"
    $installerDir = Join-Path $buildDir "installers"
    New-Item -ItemType Directory -Force -Path $distDir, $specDir, $installerDir | Out-Null

    $editions = @(
        @{
            Name = "OpenHeadMouse"
            Id = "OpenHeadMouse"
            Entry = "main.py"
            Setup = "OpenHeadMouse-windows-amd64-setup"
            HeadTracking = $true
            Viewer = $true
        },
        @{
            Name = "OpenHeadMouse (HeadMouse-only)"
            Id = "OpenHeadMouseHeadOnly"
            Entry = "main.py"
            Setup = "OpenHeadMouse-headmouse-only-windows-amd64-setup"
            HeadTracking = $true
            Viewer = $false
        },
        @{
            Name = "Dwell Click"
            Id = "DwellClick"
            Entry = "dwell_click\__main__.py"
            Setup = "DwellClick-windows-amd64-setup"
            HeadTracking = $false
            Viewer = $false
        },
        @{
            Name = "Session Viewer"
            Id = "SessionViewer"
            Entry = "session_viewer\__main__.py"
            Setup = "SessionViewer-windows-amd64-setup"
            HeadTracking = $false
            Viewer = $true
        }
    )

    foreach ($edition in $editions) {
        Write-Host "Building $($edition.Name)..."
        $buildArgs = @(
            "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed",
            "--name", $edition.Id,
            "--distpath", $distDir,
            "--workpath", (Join-Path $buildDir "work"),
            "--specpath", $specDir,
            "--paths", $PSScriptRoot
        )
        if ($edition.HeadTracking) {
            $buildArgs += @(
                "--add-data", "$(Join-Path $PSScriptRoot 'config.json');.",
                "--add-data", "$(Join-Path $PSScriptRoot 'models\face_landmarker.task');models",
                "--hidden-import", "mediapipe.tasks.c",
                "--collect-binaries", "mediapipe"
            )
            if (-not $edition.Viewer) {
                $buildArgs += @("--exclude-module", "fitts_law", "--exclude-module", "session_viewer")
            }
        }
        if ($edition.Viewer) {
            $buildArgs += @("--add-data", "$(Join-Path $PSScriptRoot 'models\canonical_face_model.obj');models")
        }
        $buildArgs += Join-Path $PSScriptRoot $edition.Entry
        & $pythonCommand @buildArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Executable build failed for $($edition.Name)."
        }

        $binaryPath = Join-Path $distDir "$($edition.Id).exe"
        if (-not (Test-Path -LiteralPath $binaryPath -PathType Leaf)) {
            throw "PyInstaller did not produce the expected executable: $binaryPath"
        }

        $scriptPath = Join-Path $installerDir "$($edition.Id).iss"
        @"
[Setup]
AppId=$($edition.Id)
AppName=$($edition.Name)
AppVersion=$Version
DefaultDirName={autopf}\$($edition.Id)
DefaultGroupName=$($edition.Name)
OutputDir=$distDir
OutputBaseFilename=$($edition.Setup)
Compression=lzma
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\$($edition.Id).exe

[Files]
Source: "$binaryPath"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\$($edition.Name)"; Filename: "{app}\$($edition.Id).exe"
Name: "{autodesktop}\$($edition.Name)"; Filename: "{app}\$($edition.Id).exe"
"@ | Set-Content -LiteralPath $scriptPath -Encoding UTF8

        & $compilerCommand $scriptPath
        if ($LASTEXITCODE -ne 0) {
            throw "Installer compilation failed for $($edition.Name)."
        }
        $installerPath = Join-Path $distDir "$($edition.Setup).exe"
        if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
            throw "Inno Setup did not produce the expected installer: $installerPath"
        }
        Write-Host "Created $installerPath"
    }

    Write-Host "All four executables and installers are available in $distDir"
} finally {
    Pop-Location
}
