<#
.SYNOPSIS
  Compile a PlatformIO project via K10 Compile Server.
.DESCRIPTION
  Upload a project directory to the K10 Compile Server, wait for
  compilation, and optionally open the Web Serial flash page.
  Uses curl.exe (built-in Windows 10 1803+) for maximum compatibility.
.PARAMETER Server
  Compile server URL (default: $env:COMPILE_SERVER or https://localhost:8900)
.PARAMETER Dir
  Project directory containing platformio.ini (default: current directory)
.PARAMETER Wait
  Wait for compilation and print result
.PARAMETER Open
  Open Web Serial flash page in browser after compile
.EXAMPLE
  .\compile-project.ps1 -Server https://192.168.1.100:8900 -Dir ../my-project -Wait
.EXAMPLE
  .\compile-project.ps1 -Dir ../examples/Blink -Wait -Open
#>

param(
  [string]$Server = $(if ($env:COMPILE_SERVER) { $env:COMPILE_SERVER } else { "https://localhost:8900" }),
  [string]$Dir = ".",
  [switch]$Wait,
  [switch]$Open
)

$ErrorActionPreference = "Stop"

function Get-JsonValue {
  param([string]$Json, [string]$Key)
  $match = [regex]::Match($Json, """$Key"":\s*""([^""]+)""")
  if ($match.Success) { return $match.Groups[1].Value }
  $match = [regex]::Match($Json, """$Key"":\s*(\d+)")
  if ($match.Success) { return $match.Groups[1].Value }
  return $null
}

# Check project
$pioIni = Join-Path $Dir "platformio.ini"
if (-not (Test-Path $pioIni)) {
  Write-Host "Error: No platformio.ini found in $Dir" -ForegroundColor Red
  exit 1
}

Write-Host "═══ K10 Compile ═══" -ForegroundColor Cyan
Write-Host "Server: $Server"
Write-Host "Project: $Dir"

# Gather files (exclude .pio, .git, build artifacts)
$files = Get-ChildItem -Path $Dir -Recurse -File | Where-Object {
  $relative = $_.FullName.Substring((Resolve-Path $Dir).Path.Length + 1)
  ($relative -notmatch '\.pio[\\/]') -and
  ($relative -notmatch '\.git[\\/]') -and
  ($relative -notmatch '\.(o|elf|map)$')
}

Write-Host "Files: $($files.Count)"

# Build curl args with relative paths preserved
$curlArgs = @("-sk", "-X", "POST", "$Server/api/compile/files")
foreach ($f in $files) {
  $relative = $f.FullName.Substring((Resolve-Path $Dir).Path.Length + 1)
  $curlArgs += @("-F", "files=@$($f.FullName);filename=$relative")
}

Write-Host "Submitting compile..."

$tempFile = [System.IO.Path]::GetTempFileName()
try {
  $proc = Start-Process -FilePath "curl.exe" -ArgumentList $curlArgs `
    -NoNewWindow -RedirectStandardOutput $tempFile -Wait -PassThru

  $response = Get-Content $tempFile -Raw
  if ($proc.ExitCode -ne 0) {
    Write-Host "Error: curl failed (exit $($proc.ExitCode))" -ForegroundColor Red
    Write-Host $response
    exit 1
  }

  $buildId = Get-JsonValue $response "build_id"
  if (-not $buildId) {
    Write-Host "Error: Failed to get build_id" -ForegroundColor Red
    Write-Host "Response: $response"
    exit 1
  }

  Write-Host "build_id: $buildId" -ForegroundColor Green
} finally {
  if (Test-Path $tempFile) { Remove-Item $tempFile -Force }
}

if (-not $Wait -and -not $Open) {
  Write-Host ""
  Write-Host "Submitted. Check status:"
  Write-Host "  curl -sk '$Server/api/build/$buildId/status'"
  Write-Host "Flash page:"
  Write-Host "  $Server/?build_id=$buildId"
  exit 0
}

# Poll for completion
if ($Wait) {
  Write-Host "Waiting..."
  while ($true) {
    Start-Sleep -Seconds 2
    try {
      $pollFile = [System.IO.Path]::GetTempFileName()
      Start-Process -FilePath "curl.exe" -ArgumentList @("-sk", "$Server/api/build/$buildId/status") `
        -NoNewWindow -RedirectStandardOutput $pollFile -Wait -PassThru | Out-Null
      $statusText = Get-Content $pollFile -Raw
      Remove-Item $pollFile -Force

      $state = Get-JsonValue $statusText "status"
      if ($state -eq "done") {
        $size = Get-JsonValue $statusText "bin_size"
        $elapsed = Get-JsonValue $statusText "elapsed"
        Write-Host "✅ Compile complete — ${size} bytes, ${elapsed}s" -ForegroundColor Green
        break
      } elseif ($state -eq "error") {
        $err = Get-JsonValue $statusText "error"
        Write-Host "❌ Compile failed: $err" -ForegroundColor Red
        exit 1
      }
    } catch {
      # Retry on transient errors
    }
  }
}

if ($Open) {
  $flashUrl = "$Server/?build_id=$buildId"
  Write-Host "Opening: $flashUrl"
  Start-Process $flashUrl
}
