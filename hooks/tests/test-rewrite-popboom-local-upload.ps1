[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$FixturePath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$hookPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\rewrite-popboom-local-upload.ps1')).Path
$fixture = Get-Item -LiteralPath (Resolve-Path -LiteralPath $FixturePath).Path
if ($fixture.PSIsContainer) {
    throw 'FixturePath must point to an image file.'
}

function Invoke-HookProcess {
    param([Parameter(Mandatory = $true)]$Event)

    $processInfo = New-Object System.Diagnostics.ProcessStartInfo
    $processInfo.FileName = 'powershell.exe'
    $processInfo.Arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$hookPath`""
    $processInfo.UseShellExecute = $false
    $processInfo.RedirectStandardInput = $true
    $processInfo.RedirectStandardOutput = $true
    $processInfo.RedirectStandardError = $true
    $processInfo.CreateNoWindow = $true

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $processInfo
    if (-not $process.Start()) {
        throw 'Failed to start the Hook process.'
    }
    $process.StandardInput.Write(($Event | ConvertTo-Json -Depth 20 -Compress))
    $process.StandardInput.Close()
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) {
        throw "Hook process exited with $($process.ExitCode): $stderr"
    }
    if ([string]::IsNullOrWhiteSpace($stdout)) {
        throw 'Hook process returned no structured output.'
    }
    return $stdout | ConvertFrom-Json
}

$fixtureUri = (New-Object System.Uri($fixture.FullName)).AbsoluteUri -replace '^file:', 'local-file:'
$sha256 = [System.Security.Cryptography.SHA256]::Create()
try {
    $sourceHash = ([System.BitConverter]::ToString($sha256.ComputeHash([System.IO.File]::ReadAllBytes($fixture.FullName)))).Replace('-', '').ToLowerInvariant()
} finally {
    $sha256.Dispose()
}

$toolNames = @('mcp__PopBoom__upload_images', 'mcp__PopBoom.upload_images', 'PopBoom/upload_images')
$base64 = $null
foreach ($toolName in $toolNames) {
    $event = @{
        session_id = 'hook-self-test'
        turn_id = 'hook-self-test-turn'
        cwd = $fixture.DirectoryName
        hook_event_name = 'PreToolUse'
        tool_name = $toolName
        tool_use_id = 'hook-self-test-call'
        model = 'hook-self-test'
        permission_mode = 'default'
        tool_input = @{
            images = @(
                @{
                    image_data = $fixtureUri
                    filename = $fixture.Name
                }
            )
        }
    }

    $result = Invoke-HookProcess $event
    $output = $result.hookSpecificOutput
    if ($output.permissionDecision -ne 'allow') {
        throw "Expected allow for $toolName, received $($output.permissionDecision): $($output.permissionDecisionReason)"
    }
    $base64 = [string]$output.updatedInput.images[0].image_data
    $roundTripBytes = [System.Convert]::FromBase64String($base64)
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $roundTripHash = ([System.BitConverter]::ToString($sha256.ComputeHash($roundTripBytes))).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha256.Dispose()
    }
    if ($sourceHash -ne $roundTripHash) {
        throw "Round-trip SHA-256 mismatch for $toolName."
    }
}

$invalidPath = Join-Path $fixture.DirectoryName ("hook-invalid-{0}.txt" -f ([guid]::NewGuid().ToString('N')))
try {
    [System.IO.File]::WriteAllText($invalidPath, 'not an image')
    $invalidFile = Get-Item -LiteralPath $invalidPath
    $invalidUri = (New-Object System.Uri($invalidFile.FullName)).AbsoluteUri -replace '^file:', 'local-file:'
    $event.tool_input.images[0].image_data = $invalidUri
    $event.tool_input.images[0].filename = $invalidFile.Name
    $blocked = Invoke-HookProcess $event
    if ($blocked.hookSpecificOutput.permissionDecision -ne 'deny') {
        throw 'Expected the invalid extension fixture to be denied.'
    }
} finally {
    Remove-Item -LiteralPath $invalidPath -Force -ErrorAction SilentlyContinue
}

$event.tool_input.images[0].image_data = $fixture.FullName
$event.tool_input.images[0].filename = $fixture.Name
$rawPathBlocked = Invoke-HookProcess $event
if ($rawPathBlocked.hookSpecificOutput.permissionDecision -ne 'deny') {
    throw 'Expected a raw Windows path in image_data to be denied.'
}

@{
    valid = $true
    fixture = $fixture.FullName
    bytes = $fixture.Length
    base64_characters = $base64.Length
    sha256 = $sourceHash
    invalid_extension_denied = $true
    raw_path_denied = $true
} | ConvertTo-Json -Compress
