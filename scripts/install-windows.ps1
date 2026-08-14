[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($env:PCIMS_PLATFORM -and $env:PCIMS_PLATFORM -ne 'Windows_NT') {
    throw 'This installer supports Windows only.'
}

$projectDirectory = Split-Path -Parent $PSScriptRoot
$pythonCommand = if ($env:PYTHON) { $env:PYTHON } else { 'python' }
$dataRootValue = if ($env:PCIMS_WINDOWS_DATA_ROOT) {
    $env:PCIMS_WINDOWS_DATA_ROOT
} else {
    if (-not $env:LOCALAPPDATA) { throw 'LOCALAPPDATA is unavailable.' }
    Join-Path $env:LOCALAPPDATA 'PCIMS'
}
if (-not [IO.Path]::IsPathRooted($dataRootValue)) {
    throw 'The Windows application data root must be absolute.'
}
$dataRoot = [IO.Path]::GetFullPath($dataRootValue)
$driveRoot = [IO.Path]::GetPathRoot($dataRoot)
if ($dataRoot.TrimEnd('\') -eq $driveRoot.TrimEnd('\')) {
    throw "Refusing unsafe application data root: $dataRoot"
}
New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null
$dataRootItem = Get-Item -LiteralPath $dataRoot -Force
if (-not $dataRootItem.PSIsContainer -or
    ($dataRootItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw "Application data root is not a regular directory: $dataRoot"
}
$dataRoot = $dataRootItem.FullName

$shortcutDirectoryValue = if ($env:PCIMS_WINDOWS_SHORTCUT_DIR) {
    $env:PCIMS_WINDOWS_SHORTCUT_DIR
} else {
    if (-not $env:APPDATA) { throw 'APPDATA is unavailable.' }
    Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
}
if (-not [IO.Path]::IsPathRooted($shortcutDirectoryValue)) {
    throw 'The Windows shortcut directory must be absolute.'
}
$shortcutDirectory = [IO.Path]::GetFullPath($shortcutDirectoryValue)
New-Item -ItemType Directory -Path $shortcutDirectory -Force | Out-Null
$shortcutDirectoryItem = Get-Item -LiteralPath $shortcutDirectory -Force
if (-not $shortcutDirectoryItem.PSIsContainer -or
    ($shortcutDirectoryItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw "Shortcut location is not a regular directory: $shortcutDirectory"
}
$shortcutDirectory = $shortcutDirectoryItem.FullName

$installRoot = Join-Path $dataRoot 'application'
$identifier = [guid]::NewGuid().ToString('N')
$stagingRoot = Join-Path $dataRoot ".application.new.$identifier"
$previousRoot = Join-Path $dataRoot ".application.previous.$identifier"
$shortcutTarget = Join-Path $shortcutDirectory 'PCIMS.lnk'
$shortcutTemporary = Join-Path $shortcutDirectory ".PCIMS.new.$identifier.lnk"
$shortcutPrevious = Join-Path $shortcutDirectory ".PCIMS.previous.$identifier.lnk"
$lockPath = Join-Path $dataRoot '.install.lock'

function Assert-ManagedDirectory([string]$Path, [switch]$AllowApplication) {
    $resolved = [IO.Path]::GetFullPath($Path)
    $parent = [IO.Path]::GetDirectoryName($resolved)
    if (-not [string]::Equals(
        $parent, $dataRoot, [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Managed installation path escaped its data root: $resolved"
    }
    $name = [IO.Path]::GetFileName($resolved)
    $allowed = $name -match '^\.application\.(new|previous)\.[a-f0-9]{32}$'
    if ($AllowApplication -and $name -eq 'application') { $allowed = $true }
    if (-not $allowed) { throw "Refusing unmanaged installation path: $resolved" }
    if (Test-Path -LiteralPath $resolved) {
        $item = Get-Item -LiteralPath $resolved -Force
        if (-not $item.PSIsContainer -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Installation path is not a regular directory: $resolved"
        }
    }
    return $resolved
}

function Remove-ManagedDirectory([string]$Path, [switch]$AllowApplication) {
    $resolved = Assert-ManagedDirectory $Path -AllowApplication:$AllowApplication
    if (Test-Path -LiteralPath $resolved) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}

function Invoke-Checked([string]$File, [string[]]$Arguments) {
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$File failed with exit code $LASTEXITCODE."
    }
}

$lockStream = $null
try {
    try {
        $lockStream = [IO.FileStream]::new(
            $lockPath,
            [IO.FileMode]::OpenOrCreate,
            [IO.FileAccess]::ReadWrite,
            [IO.FileShare]::None
        )
    } catch [IO.IOException] {
        throw 'Another PCIMS installation is already running.'
    }

    $stalePrevious = @(
        Get-ChildItem -LiteralPath $dataRoot -Force |
            Where-Object { $_.Name -like '.application.previous.*' }
    )
    if ($stalePrevious.Count -gt 1) {
        throw 'Multiple interrupted installations require manual recovery.'
    }
    if ($stalePrevious.Count -eq 1) {
        $recoveryPath = Assert-ManagedDirectory $stalePrevious[0].FullName
        if (Test-Path -LiteralPath $installRoot) {
            Assert-ManagedDirectory $installRoot -AllowApplication | Out-Null
            Remove-ManagedDirectory $recoveryPath
        } else {
            Move-Item -LiteralPath $recoveryPath -Destination $installRoot
        }
    }
    foreach ($stale in @(
        Get-ChildItem -LiteralPath $dataRoot -Force |
            Where-Object { $_.Name -like '.application.new.*' }
    )) {
        Remove-ManagedDirectory $stale.FullName
    }

    $staleShortcutPrevious = @(
        Get-ChildItem -LiteralPath $shortcutDirectory -Force -File |
            Where-Object { $_.Name -match '^\.PCIMS\.previous\.[a-f0-9]{32}\.lnk$' }
    )
    if ($staleShortcutPrevious.Count -gt 1) {
        throw 'Multiple interrupted shortcut updates require manual recovery.'
    }
    if ($staleShortcutPrevious.Count -eq 1) {
        if (Test-Path -LiteralPath $shortcutTarget) {
            Remove-Item -LiteralPath $staleShortcutPrevious[0].FullName -Force
        } else {
            Move-Item -LiteralPath $staleShortcutPrevious[0].FullName `
                -Destination $shortcutTarget
        }
    }
    Get-ChildItem -LiteralPath $shortcutDirectory -Force -File |
        Where-Object { $_.Name -match '^\.PCIMS\.new\.[a-f0-9]{32}\.lnk$' } |
        Remove-Item -Force

    Assert-ManagedDirectory $stagingRoot | Out-Null
    Assert-ManagedDirectory $previousRoot | Out-Null
    Invoke-Checked $pythonCommand @('-m', 'venv', $stagingRoot)
    $stagedPython = Join-Path $stagingRoot 'Scripts\python.exe'
    Invoke-Checked $stagedPython @(
        '-m', 'pip', 'install', '--require-hashes', '-r',
        (Join-Path $projectDirectory 'requirements.lock')
    )
    Invoke-Checked $stagedPython @(
        '-m', 'pip', 'install', '--require-hashes', '-r',
        (Join-Path $projectDirectory 'requirements-build.lock')
    )
    Invoke-Checked $stagedPython @(
        '-m', 'pip', 'install', '--no-build-isolation', '--no-deps',
        $projectDirectory
    )
    Write-Output 'Validating the staged Windows installation...'
    Invoke-Checked $stagedPython @('-m', 'pip', 'check')
    Push-Location ([IO.Path]::GetTempPath())
    try {
        Invoke-Checked $stagedPython @(
            (Join-Path $projectDirectory 'scripts\smoke-installed.py')
        )
        Write-Output 'Installed backend and Qt smoke test passed.'
    } finally {
        Pop-Location
    }

    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $null
    try {
        $shortcut = $shell.CreateShortcut($shortcutTemporary)
        $shortcut.TargetPath = Join-Path $installRoot 'Scripts\pythonw.exe'
        $shortcut.Arguments = '-m pcims.app.application'
        $shortcut.WorkingDirectory = $installRoot
        $shortcut.Description = 'PC Inventory Management'
        $shortcut.Save()
    } finally {
        if ($null -ne $shortcut) {
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shortcut)
        }
        if ($null -ne $shell) {
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell)
        }
    }

    $oldMoved = $false
    $newInstalled = $false
    $shortcutOldMoved = $false
    $shortcutPublished = $false
    try {
        if (Test-Path -LiteralPath $installRoot) {
            Assert-ManagedDirectory $installRoot -AllowApplication | Out-Null
            Move-Item -LiteralPath $installRoot -Destination $previousRoot
            $oldMoved = $true
        }
        if (Test-Path -LiteralPath $shortcutTarget) {
            Move-Item -LiteralPath $shortcutTarget -Destination $shortcutPrevious
            $shortcutOldMoved = $true
        }
        Move-Item -LiteralPath $stagingRoot -Destination $installRoot
        $newInstalled = $true
        Move-Item -LiteralPath $shortcutTemporary -Destination $shortcutTarget
        $shortcutPublished = $true
    } catch {
        $primaryError = $_
        try {
            if ($shortcutPublished -and (Test-Path -LiteralPath $shortcutTarget)) {
                Remove-Item -LiteralPath $shortcutTarget -Force
            }
            if ($shortcutOldMoved -and (Test-Path -LiteralPath $shortcutPrevious)) {
                Move-Item -LiteralPath $shortcutPrevious -Destination $shortcutTarget
            }
            if ($newInstalled -and (Test-Path -LiteralPath $installRoot)) {
                Remove-ManagedDirectory $installRoot -AllowApplication
            }
            if ($oldMoved -and (Test-Path -LiteralPath $previousRoot)) {
                Move-Item -LiteralPath $previousRoot -Destination $installRoot
            }
        } catch {
            Write-Warning "Installation rollback also failed: $_"
        }
        throw $primaryError
    }

    if ($oldMoved) {
        try { Remove-ManagedDirectory $previousRoot } catch { Write-Warning $_ }
    }
    if ($shortcutOldMoved -and (Test-Path -LiteralPath $shortcutPrevious)) {
        try { Remove-Item -LiteralPath $shortcutPrevious -Force } catch { Write-Warning $_ }
    }
    Write-Output 'PCIMS installed. Launch it from the Start Menu.'
} finally {
    if (Test-Path -LiteralPath $shortcutTemporary) {
        Remove-Item -LiteralPath $shortcutTemporary -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $stagingRoot) {
        try { Remove-ManagedDirectory $stagingRoot } catch { Write-Warning $_ }
    }
    if ($null -ne $lockStream) { $lockStream.Dispose() }
}
