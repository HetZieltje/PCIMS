Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$testRoot = Join-Path ([IO.Path]::GetTempPath()) ("pcims-installer-test-" + [guid]::NewGuid().ToString('N'))
$dataRoot = Join-Path $testRoot 'data'
$shortcutRoot = Join-Path $testRoot 'shortcuts'
$installRoot = Join-Path $dataRoot 'application'
New-Item -ItemType Directory -Path $installRoot, $shortcutRoot -Force | Out-Null
Set-Content -LiteralPath (Join-Path $installRoot 'version-marker') -Value 'old'

$oldEnvironment = @{
    PCIMS_PLATFORM = $env:PCIMS_PLATFORM
    PCIMS_WINDOWS_DATA_ROOT = $env:PCIMS_WINDOWS_DATA_ROOT
    PCIMS_WINDOWS_SHORTCUT_DIR = $env:PCIMS_WINDOWS_SHORTCUT_DIR
    PYTHON = $env:PYTHON
    PIP_NO_INDEX = $env:PIP_NO_INDEX
}
try {
    $env:PCIMS_PLATFORM = 'Windows_NT'
    $env:PCIMS_WINDOWS_DATA_ROOT = $dataRoot
    $env:PCIMS_WINDOWS_SHORTCUT_DIR = $shortcutRoot
    $env:PYTHON = if ($env:PYTHON) { $env:PYTHON } else { 'python' }
    $env:PIP_NO_INDEX = '1'
    $failed = $false
    $failureMessage = ''
    try {
        & (Join-Path $PSScriptRoot '..\scripts\install-windows.ps1')
    } catch {
        $failed = $true
        $failureMessage = $_.Exception.Message
    }
    if (-not $failed) { throw 'Installer unexpectedly accepted a failed staged install.' }
    if ($failureMessage -notlike '*failed with exit code*') {
        throw "Installer failed before dependency staging: $failureMessage"
    }
    if ((Get-Content -LiteralPath (Join-Path $installRoot 'version-marker')) -ne 'old') {
        throw 'Failed staged install changed the current application.'
    }
    if (Get-ChildItem -LiteralPath $dataRoot -Force | Where-Object Name -like '.application.*.*') {
        throw 'Failed staged install left a temporary application directory.'
    }

    $lockPath = Join-Path $dataRoot '.install.lock'
    $lock = [IO.FileStream]::new(
        $lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite,
        [IO.FileShare]::None
    )
    try {
        $lockedOut = $false
        try {
            & (Join-Path $PSScriptRoot '..\scripts\install-windows.ps1')
        } catch {
            $lockedOut = $true
        }
        if (-not $lockedOut) { throw 'Installer ignored a live installation lock.' }
    } finally {
        $lock.Dispose()
    }
    if ($env:PCIMS_RUN_REAL_INSTALL -eq '1') {
        Remove-Item Env:PIP_NO_INDEX -ErrorAction SilentlyContinue
        & (Join-Path $PSScriptRoot '..\scripts\install-windows.ps1')
        if (-not (Test-Path -LiteralPath (Join-Path $installRoot 'Scripts\python.exe'))) {
            throw 'Real installation did not publish its Python runtime.'
        }
        if (-not (Test-Path -LiteralPath (Join-Path $shortcutRoot 'PCIMS.lnk'))) {
            throw 'Real installation did not publish its Start Menu shortcut.'
        }
    }
    Write-Output 'transactional Windows installer smoke: OK'
} finally {
    foreach ($name in $oldEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $oldEnvironment[$name], 'Process')
    }
    $resolvedTestRoot = [IO.Path]::GetFullPath($testRoot)
    $temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if (-not $resolvedTestRoot.StartsWith(
        $temporaryRoot, [StringComparison]::OrdinalIgnoreCase
    ) -or [IO.Path]::GetFileName($resolvedTestRoot) -notlike 'pcims-installer-test-*') {
        throw "Refusing unsafe test cleanup target: $resolvedTestRoot"
    }
    if (Test-Path -LiteralPath $resolvedTestRoot) {
        $item = Get-Item -LiteralPath $resolvedTestRoot -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Refusing reparse-point test cleanup target: $resolvedTestRoot"
        }
        Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
    }
}
