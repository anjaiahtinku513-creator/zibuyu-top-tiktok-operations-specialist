param(
  [switch]$NoBrowser,
  [switch]$Foreground,
  [switch]$Quiet
)

$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Url = 'http://127.0.0.1:4318/'
$HealthUrl = 'http://127.0.0.1:4319/api/health'
$RuntimeRoot = Join-Path $env:LOCALAPPDATA 'ZibuyuWorkbench'
$LogsRoot = Join-Path $RuntimeRoot 'logs'
$StatePath = Join-Path $RuntimeRoot 'launcher-state.json'

function Find-Node {
  $Command = Get-Command node -ErrorAction SilentlyContinue
  if ($Command) {
    return $Command.Source
  }

  $SearchRoots = @(
    (Join-Path $env:USERPROFILE '.cache\codex-runtimes'),
    (Join-Path $env:LOCALAPPDATA 'OpenAI\Codex\runtimes')
  )
  foreach ($SearchRoot in $SearchRoots) {
    if (-not (Test-Path -LiteralPath $SearchRoot)) {
      continue
    }
    $NodePath = Get-ChildItem -LiteralPath $SearchRoot -Recurse -Filter node.exe -File -ErrorAction SilentlyContinue |
      Sort-Object LastWriteTime -Descending |
      Select-Object -First 1 -ExpandProperty FullName
    if ($NodePath) {
      return $NodePath
    }
  }

  throw 'Node.js was not found. Open this project from Codex Desktop and try again.'
}

function Test-Workbench {
  try {
    $Health = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 2
    $null = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2
    return [bool]$Health.ok
  } catch {
    return $false
  }
}

function Open-Workbench {
  if (-not $NoBrowser) {
    Start-Process $Url
  }
}

if (Test-Workbench) {
  if (-not $Quiet) {
    Write-Host "Zibuyu Workbench is already running at $Url"
  }
  Open-Workbench
  exit 0
}

$NodePath = Find-Node
$DevScript = Join-Path $ProjectRoot 'scripts\dev.mjs'

if ($Foreground) {
  Set-Location $ProjectRoot
  & $NodePath $DevScript
  exit $LASTEXITCODE
}

New-Item -ItemType Directory -Path $LogsRoot -Force | Out-Null
$Timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$StdoutPath = Join-Path $LogsRoot "workbench-$Timestamp.out.log"
$StderrPath = Join-Path $LogsRoot "workbench-$Timestamp.err.log"

$Process = Start-Process `
  -FilePath $NodePath `
  -ArgumentList $DevScript `
  -WorkingDirectory $ProjectRoot `
  -WindowStyle Hidden `
  -RedirectStandardOutput $StdoutPath `
  -RedirectStandardError $StderrPath `
  -PassThru

@{
  pid = $Process.Id
  projectRoot = $ProjectRoot
  startedAt = (Get-Date).ToString('o')
  stdout = $StdoutPath
  stderr = $StderrPath
} | ConvertTo-Json | Set-Content -LiteralPath $StatePath -Encoding utf8

$Deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $Deadline) {
  Start-Sleep -Milliseconds 500
  if (Test-Workbench) {
    if (-not $Quiet) {
      Write-Host "Zibuyu Workbench started in the background at $Url"
      Write-Host "Launcher PID: $($Process.Id)"
    }
    Open-Workbench
    exit 0
  }
  if ($Process.HasExited) {
    break
  }
}

$ErrorDetail = ''
if (Test-Path -LiteralPath $StderrPath) {
  $ErrorDetail = (Get-Content -LiteralPath $StderrPath -Tail 20) -join [Environment]::NewLine
}
throw "Zibuyu Workbench failed to start. Log: $StderrPath`n$ErrorDetail"
