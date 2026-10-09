$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$AppDir = (Resolve-Path $PSScriptRoot).Path
$RuntimeRoot = Join-Path $env:LOCALAPPDATA 'SSM-SpiceConex'
$VenvDir = Join-Path $RuntimeRoot 'venv'
$PluginMinVersion = [version]'1.2.764.0'

function Write-Step {
    param([string]$Message)
    Write-Host $Message
}

function Show-Banner {
    Write-Host ''
    Write-Host '  _____ _____  ___ ____ _____ ____ ___  _   _  ___  _  _ ' -ForegroundColor DarkYellow
    Write-Host ' / ___|  ___|/ _ \ / ___| ____/ ___/ _ \| \ | |/ _ \| \| |' -ForegroundColor DarkYellow
    Write-Host '| (__ | |__ | | | | |   |  _|| |  | | | |  \| | | | | .` |' -ForegroundColor DarkYellow
    Write-Host ' \___ \|  _|| |_| | |___| |__| |__| |_| | |\  | |_| | |\  |' -ForegroundColor DarkYellow
    Write-Host ' |___/|_|   \___/ \____|_____|\____\___/|_| \_|\___/|_| \_|' -ForegroundColor DarkYellow
    Write-Host '                         SPICECONEX' -ForegroundColor Gray
    Write-Host ''
}

function Test-Command {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Test-Python {
    param([string]$Command)
    try {
        & $Command -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' *> $null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $parts = @()
    if ($machine) { $parts += $machine }
    if ($user) { $parts += $user }
    $env:Path = $parts -join ';'
}

function Test-AwsCliV2 {
    if (-not (Test-Command 'aws')) { return $false }
    try {
        $raw = (& aws --version 2>&1 | Out-String).Trim()
        return ($raw -match 'aws-cli/2\.')
    } catch {
        return $false
    }
}

function Get-PluginVersion {
    if (-not (Test-Command 'session-manager-plugin')) { return $null }
    try {
        $raw = (& session-manager-plugin --version 2>&1 | Out-String).Trim()
        $match = [regex]::Match($raw, '\d+\.\d+\.\d+\.\d+')
        if ($match.Success) { return [version]$match.Value }
    } catch {}
    return $null
}

function Run-Quiet {
    param(
        [string]$Label,
        [string]$FilePath,
        [string[]]$ArgumentList = @()
    )

    $stdout = Join-Path $env:TEMP ('spiceconex-' + [guid]::NewGuid().ToString('N') + '.out')
    $stderr = Join-Path $env:TEMP ('spiceconex-' + [guid]::NewGuid().ToString('N') + '.err')

    Write-Host ('[|] ' + $Label) -NoNewline
    $process = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -Wait -PassThru -NoNewWindow -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    if ($process.ExitCode -ne 0) {
        Write-Host "`r[FAIL] $Label" -ForegroundColor Red
        if (Test-Path $stderr) { Get-Content $stderr | Select-Object -Last 30 | Write-Host }
        elseif (Test-Path $stdout) { Get-Content $stdout | Select-Object -Last 30 | Write-Host }
        Remove-Item $stdout,$stderr -Force -ErrorAction SilentlyContinue
        throw "Installation step failed: $Label"
    }
    Write-Host "`r[OK] $Label" -ForegroundColor Green
    Remove-Item $stdout,$stderr -Force -ErrorAction SilentlyContinue
}

Show-Banner
Write-Step 'Detected platform: Windows'
Write-Step ('Application directory: ' + $AppDir)

# Python
$PythonCommand = $null
foreach ($candidate in @('py', 'python')) {
    if (Test-Command $candidate -and (Test-Python $candidate)) {
        $PythonCommand = $candidate
        break
    }
}

if (-not $PythonCommand) {
    if (Test-Command 'winget') {
        Write-Step 'Python 3.10+ not found. Installing Python 3.12...'
        Run-Quiet 'Installing Python 3.12' 'winget' @('install','--id','Python.Python.3.12','--exact','--scope','user','--accept-package-agreements','--accept-source-agreements','--silent')
    } elseif (Test-Command 'choco') {
        Run-Quiet 'Installing Python 3.12' 'choco' @('install','python','--version=3.12.10','-y','--no-progress')
    } else {
        throw 'Python 3.10+ is required. Install Python from python.org, winget or Chocolatey, then rerun setup.ps1.'
    }

    Refresh-Path
    foreach ($candidate in @('py', 'python')) {
        if (Test-Command $candidate -and (Test-Python $candidate)) {
            $PythonCommand = $candidate
            break
        }
    }
}

if (-not $PythonCommand) {
    throw 'Python installation completed, but Python 3.10+ could not be located. Open a new PowerShell session and rerun setup.ps1.'
}
Write-Step ('Using Python: ' + $PythonCommand)

# Git
if (Test-Command 'git') {
    Write-Step ('Git detected: ' + ((& git --version 2>&1 | Out-String).Trim()))
} else {
    $gitInstalled = $false
    if (Test-Command 'winget') {
        try { Run-Quiet 'Installing Git' 'winget' @('install','--id','Git.Git','--exact','--scope','user','--accept-package-agreements','--accept-source-agreements','--silent'); Refresh-Path; $gitInstalled = Test-Command 'git' } catch { Write-Step ('winget Git installation failed: ' + $_.Exception.Message) }
    }
    if (-not $gitInstalled -and (Test-Command 'choco')) {
        Run-Quiet 'Installing Git' 'choco' @('install','git','-y','--no-progress'); Refresh-Path; $gitInstalled = Test-Command 'git'
    }
    if (-not $gitInstalled) { throw 'Git is required for in-app updates. Install Git for Windows and rerun setup.ps1.' }
    Write-Step ('Git installed: ' + ((& git --version 2>&1 | Out-String).Trim()))
}

# AWS CLI v2
if (Test-AwsCliV2) {
    Write-Step ('AWS CLI v2 detected: ' + ((& aws --version 2>&1 | Out-String).Trim()))
} else {
    Write-Step 'AWS CLI v2 not found. Installing it...'
    $installed = $false

    if (Test-Command 'winget') {
        try {
            Run-Quiet 'Installing AWS CLI v2' 'winget' @('install','--id','Amazon.AWSCLI','--exact','--accept-package-agreements','--accept-source-agreements','--silent')
            Refresh-Path
            $installed = Test-AwsCliV2
        } catch {
            Write-Step ('winget AWS CLI installation failed: ' + $_.Exception.Message)
        }
    }

    if (-not $installed) {
        $installer = Join-Path $env:TEMP 'AWSCLIV2.msi'
        Write-Step 'Downloading official AWS CLI v2 installer...'
        Invoke-WebRequest -Uri 'https://awscli.amazonaws.com/AWSCLIV2.msi' -OutFile $installer
        Write-Step 'Installing AWS CLI v2...'
        $msi = Start-Process -FilePath 'msiexec.exe' -ArgumentList @('/i',$installer,'/quiet','/norestart') -Verb RunAs -Wait -PassThru
        if ($msi.ExitCode -ne 0) { throw "AWS CLI installer failed with exit code $($msi.ExitCode)." }
        Refresh-Path
        $installed = Test-AwsCliV2
    }

    if (-not $installed) { throw 'AWS CLI v2 could not be installed or located in PATH.' }
    Write-Step ('AWS CLI installed: ' + ((& aws --version 2>&1 | Out-String).Trim()))
}

# Session Manager plugin
$PluginVersion = Get-PluginVersion
if ($PluginVersion -and $PluginVersion -ge $PluginMinVersion) {
    Write-Step ('Session Manager plugin detected: ' + $PluginVersion)
} else {
    if ($PluginVersion) {
        Write-Step ('Session Manager plugin ' + $PluginVersion + ' is older than ' + $PluginMinVersion + '. Updating...')
    } else {
        Write-Step 'Session Manager plugin not found. Installing it...'
    }

    $pluginInstaller = Join-Path $env:TEMP 'SessionManagerPluginSetup.exe'
    Invoke-WebRequest -Uri 'https://s3.amazonaws.com/session-manager-downloads/plugin/latest/windows/SessionManagerPluginSetup.exe' -OutFile $pluginInstaller
    Write-Step 'Installing Session Manager plugin. Windows may display a UAC prompt.'
    $pluginProcess = Start-Process -FilePath $pluginInstaller -Verb RunAs -Wait -PassThru
    if ($pluginProcess.ExitCode -ne 0) { throw "Session Manager plugin installer failed with exit code $($pluginProcess.ExitCode)." }
    Refresh-Path
    $PluginVersion = Get-PluginVersion
    if (-not $PluginVersion -or $PluginVersion -lt $PluginMinVersion) {
        throw ('Session Manager plugin installation could not be verified. Required >= ' + $PluginMinVersion)
    }
    Write-Step ('Session Manager plugin installed: ' + $PluginVersion)
}

# Python environment
New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
if (Test-Path $VenvDir) {
    Write-Step ('Reusing Python environment: ' + $VenvDir)
} else {
    Run-Quiet 'Creating Python virtual environment' $PythonCommand @('-m','venv',$VenvDir)
}

$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
if (-not (Test-Path $VenvPython)) { throw ('Python virtual environment is incomplete: ' + $VenvPython) }

Run-Quiet 'Installing Python package manager' $VenvPython @('-m','pip','install','--upgrade','pip','--disable-pip-version-check','--no-input')
Run-Quiet 'Installing SpiceConex Python modules' $VenvPython @('-m','pip','install','-r',(Join-Path $AppDir 'requirements.txt'),'--disable-pip-version-check','--no-input')

# Launcher
$CmdPath = Join-Path $AppDir 'spiceconex.cmd'
$LauncherTarget = Join-Path $AppDir 'ssm_spiceconex.py'
$launcherText = '@echo off' + [Environment]::NewLine + '"' + $VenvPython + '" "' + $LauncherTarget + '" %*' + [Environment]::NewLine
[IO.File]::WriteAllText($CmdPath, $launcherText, [Text.Encoding]::ASCII)

$UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$entries = @()
if ($UserPath) { $entries = $UserPath -split ';' | Where-Object { $_ -and $_.Trim() } }
if ($entries -notcontains $AppDir) {
    [Environment]::SetEnvironmentVariable('Path', (($entries + $AppDir) -join ';'), 'User')
}
$env:Path = $AppDir + ';' + $env:Path

Write-Host ''
Write-Host '========================================' -ForegroundColor DarkYellow
Write-Host ' SPICECONEX READY' -ForegroundColor DarkYellow
Write-Host '========================================' -ForegroundColor DarkYellow
Write-Step ('AWS CLI: ' + ((& aws --version 2>&1 | Out-String).Trim()))
Write-Step ('Session Manager plugin: ' + $PluginVersion)
Write-Step ('Python environment: ' + $VenvDir)
Write-Step ('Installed command: ' + $CmdPath)
Write-Host ''
Write-Host 'Setup complete.' -ForegroundColor Green
Write-Host 'Open a new terminal and run:'
Write-Host '  spiceconex' -ForegroundColor DarkYellow
Write-Host ''
Write-Host 'Tip: spiceconex --demo' -ForegroundColor Gray
