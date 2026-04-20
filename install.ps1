$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$TargetDir = if ($args.Count -gt 0 -and $args[0]) { $args[0] } else { Join-Path $HOME "bin" }

New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null

$LegacyTargets = @(
    (Join-Path $TargetDir "__pycache__"),
    (Join-Path $TargetDir "codex-use-api"),
    (Join-Path $TargetDir "codex-use-chatgpt")
)
foreach ($LegacyTarget in $LegacyTargets) {
    if (Test-Path $LegacyTarget) {
        Remove-Item -Recurse -Force $LegacyTarget
    }
}

Copy-Item (Join-Path $ScriptDir "codex_mode.py") (Join-Path $TargetDir "codex_mode.py") -Force
Copy-Item (Join-Path $ScriptDir "VERSION") (Join-Path $TargetDir "VERSION") -Force
Copy-Item (Join-Path $ScriptDir "codex-mode.ps1") (Join-Path $TargetDir "codex-mode.ps1") -Force
Copy-Item (Join-Path $ScriptDir "codex-mode.cmd") (Join-Path $TargetDir "codex-mode.cmd") -Force
Set-Content -Path (Join-Path $TargetDir ".codex-mode-source") -Value $ScriptDir -Encoding utf8

Write-Host "Installed:"
Write-Host "  $TargetDir\codex-mode.ps1"
Write-Host "  $TargetDir\codex-mode.cmd"
Write-Host "  $TargetDir\codex_mode.py"
Write-Host "  $TargetDir\VERSION"
Write-Host "  $TargetDir\.codex-mode-source"

$PathEntries = ($env:PATH -split ';') | Where-Object { $_ -ne "" }
if ($PathEntries -contains $TargetDir) {
    Write-Host "PATH already contains $TargetDir"
} else {
    Write-Host "If you want global access, add this directory to PATH:"
    Write-Host "  $TargetDir"
}
