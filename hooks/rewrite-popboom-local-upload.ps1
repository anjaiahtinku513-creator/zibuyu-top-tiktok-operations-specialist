[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$LocalFilePrefix = 'local-file://'
$MaximumImageBytes = 20 * 1024 * 1024
$SupportedExtensions = @('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp')
$SupportedToolNames = @(
    'mcp__PopBoom__upload_images',
    'mcp__PopBoom.upload_images',
    'PopBoom/upload_images'
)

function Write-HookResult {
    param([Parameter(Mandatory = $true)]$Payload)

    $json = $Payload | ConvertTo-Json -Depth 100 -Compress
    [Console]::Out.Write($json)
}

function Stop-HookCall {
    param([Parameter(Mandatory = $true)][string]$Reason)

    $cleanReason = ($Reason -replace '[\r\n]+', ' ').Trim()
    if ($cleanReason.Length -gt 500) {
        $cleanReason = $cleanReason.Substring(0, 500)
    }

    Write-HookResult @{
        hookSpecificOutput = @{
            hookEventName = 'PreToolUse'
            permissionDecision = 'deny'
            permissionDecisionReason = "PopBoom local upload blocked: $cleanReason"
        }
    }
    exit 0
}

function Get-NormalizedPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
}

function Test-IsWithinRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )

    $normalizedPath = Get-NormalizedPath $Path
    $normalizedRoot = Get-NormalizedPath $Root
    if ($normalizedPath.Equals($normalizedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }

    $rootPrefix = $normalizedRoot + [System.IO.Path]::DirectorySeparatorChar
    return $normalizedPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-AllowedRoots {
    param([Parameter(Mandatory = $true)]$HookEvent)

    $roots = New-Object System.Collections.Generic.List[string]
    if ($HookEvent.PSObject.Properties['cwd'] -and
        -not [string]::IsNullOrWhiteSpace([string]$HookEvent.cwd)) {
        $roots.Add((Get-NormalizedPath ([string]$HookEvent.cwd)))
    }

    $codexHome = $env:CODEX_HOME
    if ([string]::IsNullOrWhiteSpace($codexHome)) {
        if ([string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
            throw 'Neither CODEX_HOME nor USERPROFILE is available.'
        }
        $codexHome = Join-Path $env:USERPROFILE '.codex'
    }
    $roots.Add((Get-NormalizedPath (Join-Path $codexHome 'zibuyu-runs')))

    return @($roots | Select-Object -Unique)
}

function Resolve-LocalFileReference {
    param(
        [Parameter(Mandatory = $true)][string]$Reference,
        [Parameter(Mandatory = $true)][string[]]$AllowedRoots
    )

    $uri = New-Object System.Uri($Reference, [System.UriKind]::Absolute)
    if (-not $uri.Scheme.Equals('local-file', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'The local reference must use the local-file URI scheme.'
    }
    if (-not [string]::IsNullOrEmpty($uri.Host) -or
        -not [string]::IsNullOrEmpty($uri.Query) -or
        -not [string]::IsNullOrEmpty($uri.Fragment)) {
        throw 'Network hosts, query strings, and fragments are not allowed in local-file references.'
    }

    $decodedPath = [System.Uri]::UnescapeDataString($uri.AbsolutePath)
    if ($decodedPath -match '^/[A-Za-z]:/') {
        $decodedPath = $decodedPath.Substring(1)
    }
    $candidate = $decodedPath.Replace('/', [System.IO.Path]::DirectorySeparatorChar)
    if (-not [System.IO.Path]::IsPathRooted($candidate)) {
        throw 'The local-file reference must resolve to an absolute path.'
    }

    $fullPath = Get-NormalizedPath $candidate
    $insideAllowedRoot = $false
    foreach ($root in $AllowedRoots) {
        if (Test-IsWithinRoot -Path $fullPath -Root $root) {
            $insideAllowedRoot = $true
            break
        }
    }
    if (-not $insideAllowedRoot) {
        throw 'The local image must be staged under the current workspace or CODEX_HOME/zibuyu-runs.'
    }

    $file = Get-Item -LiteralPath $fullPath -Force
    if ($file.PSIsContainer) {
        throw 'The local-file reference points to a directory.'
    }
    if (($file.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw 'Symbolic links and reparse-point files are not accepted for PopBoom upload.'
    }
    return $file
}

function Test-BytePrefix {
    param(
        [Parameter(Mandatory = $true)][byte[]]$Bytes,
        [Parameter(Mandatory = $true)][byte[]]$Prefix
    )

    if ($Bytes.Length -lt $Prefix.Length) {
        return $false
    }
    for ($index = 0; $index -lt $Prefix.Length; $index++) {
        if ($Bytes[$index] -ne $Prefix[$index]) {
            return $false
        }
    }
    return $true
}

function Assert-ImageSignature {
    param(
        [Parameter(Mandatory = $true)][byte[]]$Bytes,
        [Parameter(Mandatory = $true)][string]$Extension
    )

    $ascii = [System.Text.Encoding]::ASCII
    $valid = $false
    switch ($Extension) {
        '.png' {
            $valid = Test-BytePrefix $Bytes ([byte[]](0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a))
        }
        { $_ -in @('.jpg', '.jpeg') } {
            $valid = Test-BytePrefix $Bytes ([byte[]](0xff, 0xd8, 0xff))
        }
        '.webp' {
            $valid = $Bytes.Length -ge 12 -and
                (Test-BytePrefix $Bytes ($ascii.GetBytes('RIFF'))) -and
                ($ascii.GetString($Bytes, 8, 4) -eq 'WEBP')
        }
        '.gif' {
            $valid = $Bytes.Length -ge 6 -and
                ($ascii.GetString($Bytes, 0, 6) -in @('GIF87a', 'GIF89a'))
        }
        '.bmp' {
            $valid = Test-BytePrefix $Bytes ($ascii.GetBytes('BM'))
        }
    }

    if (-not $valid) {
        throw "File content does not match the $Extension image extension."
    }
}

function Assert-CompatibleFilename {
    param(
        [Parameter(Mandatory = $true)][string]$Filename,
        [Parameter(Mandatory = $true)][string]$SourceExtension
    )

    if ([System.IO.Path]::GetFileName($Filename) -ne $Filename) {
        throw 'filename must contain only a base name, not a path.'
    }
    $filenameExtension = [System.IO.Path]::GetExtension($Filename).ToLowerInvariant()
    $bothJpeg = $SourceExtension -in @('.jpg', '.jpeg') -and $filenameExtension -in @('.jpg', '.jpeg')
    if (-not $bothJpeg -and $filenameExtension -ne $SourceExtension) {
        throw 'filename extension must match the local image extension.'
    }
}

try {
    $rawInput = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($rawInput)) {
        exit 0
    }

    $hookEvent = $rawInput | ConvertFrom-Json
    if (-not $hookEvent.PSObject.Properties['tool_name'] -or
        [string]$hookEvent.tool_name -notin $SupportedToolNames) {
        exit 0
    }
    if (-not $hookEvent.PSObject.Properties['tool_input'] -or $null -eq $hookEvent.tool_input) {
        throw 'Missing PopBoom tool_input.'
    }
    if (-not $hookEvent.tool_input.PSObject.Properties['images']) {
        throw 'Missing upload_images images array.'
    }

    $images = @($hookEvent.tool_input.images)
    if ($images.Count -lt 1 -or $images.Count -gt 10) {
        throw 'upload_images must contain between 1 and 10 items.'
    }

    $allowedRoots = Get-AllowedRoots $hookEvent
    $rewritten = $false
    foreach ($image in $images) {
        if ($null -eq $image -or -not $image.PSObject.Properties['image_data']) {
            throw 'Each upload_images item must contain image_data.'
        }

        $imageData = [string]$image.image_data
        if (-not $imageData.StartsWith($LocalFilePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            if ($imageData -match '^[A-Za-z]:[\\/]' -or
                $imageData.StartsWith('\\\\') -or
                $imageData.StartsWith('file://', [System.StringComparison]::OrdinalIgnoreCase)) {
                throw 'Raw local paths are not valid image_data. Use an absolute local-file:/// URI.'
            }
            continue
        }

        $file = Resolve-LocalFileReference -Reference $imageData -AllowedRoots $allowedRoots
        $extension = $file.Extension.ToLowerInvariant()
        if ($extension -notin $SupportedExtensions) {
            throw "Unsupported local image extension: $extension"
        }
        if ($file.Length -le 0) {
            throw 'The local image is empty.'
        }
        if ($file.Length -gt $MaximumImageBytes) {
            throw "The original image exceeds the documented 20 MB image limit ($($file.Length) bytes)."
        }

        $bytes = [System.IO.File]::ReadAllBytes($file.FullName)
        Assert-ImageSignature -Bytes $bytes -Extension $extension

        $filenameProperty = $image.PSObject.Properties['filename']
        if ($null -eq $filenameProperty -or [string]::IsNullOrWhiteSpace([string]$image.filename)) {
            $image | Add-Member -NotePropertyName filename -NotePropertyValue $file.Name -Force
        } else {
            Assert-CompatibleFilename -Filename ([string]$image.filename) -SourceExtension $extension
        }

        $image.image_data = [System.Convert]::ToBase64String($bytes)
        $rewritten = $true
    }

    if (-not $rewritten) {
        exit 0
    }

    Write-HookResult @{
        hookSpecificOutput = @{
            hookEventName = 'PreToolUse'
            permissionDecision = 'allow'
            updatedInput = $hookEvent.tool_input
        }
    }
} catch {
    Stop-HookCall $_.Exception.Message
}
