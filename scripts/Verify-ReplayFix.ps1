[CmdletBinding()]
param()

# Runs the focused tests for the replay/login fix and saves everything to one
# text file so the result can be reviewed without reading the console.
# It never commits, never pushes, and never touches a corporate URL.

$ErrorActionPreference = "Continue"
$env:PYTHONIOENCODING = "utf-8"
$env:SMARTOPS_DISABLE_WORKER = "1"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$outDir = Join-Path $env:LOCALAPPDATA "SmartOps\verify"
New-Item -ItemType Directory -Path $outDir -Force | Out-Null
$outFile = Join-Path $outDir "verify-latest.txt"
Set-Location -LiteralPath $projectRoot

function Section([string]$Title) {
    $line = "`n===== $Title =====`n"
    Write-Host $line -ForegroundColor Cyan
    Add-Content -LiteralPath $outFile -Value $line -Encoding UTF8
}

function Run([string]$Label, [string[]]$Arguments) {
    Section $Label
    $text = (& python @Arguments 2>&1 | Out-String)
    $exit = $LASTEXITCODE
    Write-Host $text
    Add-Content -LiteralPath $outFile -Value $text -Encoding UTF8
    Add-Content -LiteralPath $outFile -Value "exit code: $exit" -Encoding UTF8
}

Set-Content -LiteralPath $outFile -Value "SmartOps verification $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -Encoding UTF8

Section "git status"
$status = (& git status --short 2>&1 | Out-String)
Write-Host $status
Add-Content -LiteralPath $outFile -Value $status -Encoding UTF8

Section "git diff --stat"
$stat = (& git diff --stat 2>&1 | Out-String)
Write-Host $stat
Add-Content -LiteralPath $outFile -Value $stat -Encoding UTF8

Run "focused tests" @(
    "-m", "pytest", "-q",
    "tests\test_auth_classifier.py",
    "tests\test_popup_login.py",
    "tests\test_recording_authentication.py",
    "tests\test_profiles.py",
    "tests\test_credentials.py",
    "tests\test_contract_hardening.py"
)

for ($i = 1; $i -le 3; $i++) {
    Run "browser session auth, run $i of 3" @("-m", "pytest", "-q", "tests\test_browser_session_auth.py")
}

Section "done"
Write-Host "Results saved to: $outFile" -ForegroundColor Green
Add-Content -LiteralPath $outFile -Value "Results saved to: $outFile" -Encoding UTF8
Write-Host "You can close this window."
Read-Host "Press Enter to close"
