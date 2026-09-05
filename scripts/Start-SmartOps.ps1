[CmdletBinding()]
param(
    [int]$Port = 0,
    [int]$StartupTimeoutSeconds = 60,
    [switch]$NoBrowser,
    [switch]$SkipInstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
# Child Python tools (including the approved Chrome launcher) may print Arabic
# page titles. Force UTF-8 so legacy Windows PowerShell does not raise cp1252
# encoding errors after a successful browser action.
$env:PYTHONIOENCODING = "utf-8"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runtimeRoot = Join-Path $env:LOCALAPPDATA "SmartOps\launcher"
$stdoutLog = Join-Path $runtimeRoot "server.stdout.log"
$stderrLog = Join-Path $runtimeRoot "server.stderr.log"
$launcherLog = Join-Path $runtimeRoot "launcher.log"
$pidFile = Join-Path $runtimeRoot "server.pid"

function Write-LauncherMessage {
    param([string]$Message, [ConsoleColor]$Color = [ConsoleColor]::Gray)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host $Message -ForegroundColor $Color
    Add-Content -LiteralPath $launcherLog -Value "[$timestamp] $Message" -Encoding UTF8
}

function Get-PythonExecutable {
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "Python was not found. Install Python 3.11 or newer, then double-click START again."
    }
    return $command.Source
}

function Test-SmartOpsHealth {
    param([string]$HealthUrl)
    try {
        $health = Invoke-RestMethod -UseBasicParsing -Uri $HealthUrl -TimeoutSec 2
        return ($health.status -eq "ok" -and $health.service -eq "smartops")
    }
    catch {
        return $false
    }
}

function Test-LocalPortInUse {
    param([int]$PortNumber)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $pending = $client.BeginConnect("127.0.0.1", $PortNumber, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne(500)) {
            return $false
        }
        $client.EndConnect($pending)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Close()
    }
}

function Find-Chrome {
    <#
        Locate Google Chrome on THIS machine. The launcher used to point at one
        developer's folder ("d:\WORK\...") and throw when it was missing, which
        made START.cmd fail on every other computer. Nothing here is specific to
        a machine: the registry entry Chrome writes on install, the two standard
        install locations, and finally PATH.
    #>
    $registryKeys = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
        "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
    )
    foreach ($key in $registryKeys) {
        try {
            $path = (Get-ItemProperty -LiteralPath $key -ErrorAction Stop)."(default)"
            if ($path -and (Test-Path -LiteralPath $path -PathType Leaf)) { return $path }
        }
        catch { }
    }
    $candidates = @(
        (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe"),
        (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe")
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) { return $candidate }
    }
    $command = Get-Command chrome.exe -ErrorAction SilentlyContinue
    if ($null -ne $command) { return $command.Source }
    return $null
}

try {
    New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
    Set-Location -LiteralPath $projectRoot
    Write-LauncherMessage "Starting SmartOps..." Cyan

    $pythonExe = Get-PythonExecutable
    $versionText = (& $pythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Python could not run: $versionText"
    }
    $versionParts = $versionText.Split(".")
    if ([int]$versionParts[0] -lt 3 -or ([int]$versionParts[0] -eq 3 -and [int]$versionParts[1] -lt 11)) {
        throw "SmartOps needs Python 3.11 or newer. Found Python $versionText."
    }

    & $pythonExe -c "import smartops, fastapi, uvicorn, playwright" 2>$null
    if ($LASTEXITCODE -ne 0) {
        if ($SkipInstall) {
            throw "SmartOps dependencies are missing and automatic installation was disabled."
        }
        Write-LauncherMessage "First run: installing or repairing SmartOps dependencies..." Yellow
        $editableSpec = "$projectRoot[dev]"
        & $pythonExe -m pip install -e $editableSpec
        if ($LASTEXITCODE -ne 0) {
            throw "Dependency installation failed. Run: python -m pip install -e `".[dev]`""
        }
    }

    if ($Port -le 0) {
        $portText = (& $pythonExe -c "from smartops.config import load_settings; print(load_settings().app.port)" 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or -not [int]::TryParse($portText, [ref]$Port)) {
            throw "Could not determine the SmartOps port: $portText"
        }
    }
    if ($Port -lt 1 -or $Port -gt 65535) {
        throw "Invalid SmartOps port: $Port"
    }

    $healthUrl = "http://127.0.0.1:$Port/health"
    $appUrl = "http://127.0.0.1:$Port/app/index.html"

    if (Test-SmartOpsHealth -HealthUrl $healthUrl) {
        Write-LauncherMessage "SmartOps is already running on port $Port." Green
    }
    else {
        if (Test-LocalPortInUse -PortNumber $Port) {
            throw "Port $Port is already used by another application. Close it or set SMARTOPS_PORT to a free port."
        }

        $env:SMARTOPS_PORT = [string]$Port
        Write-LauncherMessage "Launching the local server on port $Port..." Cyan
        $serverProcess = Start-Process -FilePath $pythonExe `
            -ArgumentList @("-m", "smartops", "serve") `
            -WorkingDirectory $projectRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $stdoutLog `
            -RedirectStandardError $stderrLog `
            -PassThru
        Set-Content -LiteralPath $pidFile -Value $serverProcess.Id -Encoding ASCII

        $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
        while ((Get-Date) -lt $deadline) {
            if (Test-SmartOpsHealth -HealthUrl $healthUrl) {
                break
            }
            if ($serverProcess.HasExited) {
                $details = ""
                if (Test-Path -LiteralPath $stderrLog) {
                    $details = (Get-Content -LiteralPath $stderrLog -Tail 20 | Out-String).Trim()
                }
                throw "SmartOps server stopped during startup. $details"
            }
            Start-Sleep -Milliseconds 500
            $serverProcess.Refresh()
        }

        if (-not (Test-SmartOpsHealth -HealthUrl $healthUrl)) {
            throw "SmartOps did not become healthy within $StartupTimeoutSeconds seconds. Check $stderrLog"
        }
        Write-LauncherMessage "SmartOps server is healthy." Green
    }

    if (-not $NoBrowser) {
        # A browser that will not open is never a reason to fail a server that
        # started correctly: the address is printed either way, so the user can
        # always get in.
        $chrome = Find-Chrome
        if ($null -ne $chrome) {
            Write-LauncherMessage "Opening SmartOps in Google Chrome..." Cyan
            Start-Process -FilePath $chrome -ArgumentList $appUrl | Out-Null
        }
        else {
            Write-LauncherMessage "Google Chrome was not found; opening your default browser instead." Yellow
            try { Start-Process $appUrl | Out-Null }
            catch { Write-LauncherMessage "Could not open a browser automatically. Open this address yourself: $appUrl" Yellow }
        }
    }

    Write-LauncherMessage "SmartOps is ready: $appUrl" Green
    Write-LauncherMessage "Scheduled automations run automatically while this server is running." DarkGray
    Write-LauncherMessage "Logs: $runtimeRoot" DarkGray
    exit 0
}
catch {
    $message = $_.Exception.Message
    try {
        Write-LauncherMessage "START failed: $message" Red
        Write-LauncherMessage "Logs: $runtimeRoot" Yellow
    }
    catch {
        Write-Host "START failed: $message" -ForegroundColor Red
    }
    exit 1
}
