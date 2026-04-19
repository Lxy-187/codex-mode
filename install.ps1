$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$TargetDir = if ($args.Count -gt 0 -and $args[0]) { $args[0] } else { Join-Path $HOME "bin" }

New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null

Copy-Item (Join-Path $ScriptDir "codex_mode.py") (Join-Path $TargetDir "codex_mode.py") -Force
Copy-Item (Join-Path $ScriptDir "codex-mode.ps1") (Join-Path $TargetDir "codex-mode.ps1") -Force
Copy-Item (Join-Path $ScriptDir "codex-mode.cmd") (Join-Path $TargetDir "codex-mode.cmd") -Force

Write-Host "Installed:"
Write-Host "  $TargetDir\codex-mode.ps1"
Write-Host "  $TargetDir\codex-mode.cmd"
Write-Host "  $TargetDir\codex_mode.py"

$PathEntries = ($env:PATH -split ';') | Where-Object { $_ -ne "" }
if ($PathEntries -contains $TargetDir) {
    Write-Host "PATH already contains $TargetDir"
} else {
    Write-Host "If you want global access, add this directory to PATH:"
    Write-Host "  $TargetDir"
}
