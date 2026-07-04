<#
.SYNOPSIS
  Compile a PlatformIO project via K10 Compile Server.
.DESCRIPTION
  Upload a project directory to the K10 Compile Server, wait for
  compilation, and optionally open the Web Serial flash page.
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

# Check project
$pioIni = Join-Path $Dir "platformio.ini"
if (-not (Test-Path $pioIni)) {
  Write-Error "No platformio.ini found in $Dir"
  exit 1
}

Write-Host "═══ K10 Compile ═══" -ForegroundColor Cyan
Write-Host "Server: $Server"
Write-Host "Project: $Dir"

# Gather files (exclude .pio, .git, build artifacts)
$exclude = @('.pio', '.git', '.o', '.elf', '.map')
$files = Get-ChildItem -Path $Dir -Recurse -File | Where-Object {
  $relative = $_.FullName.Substring((Resolve-Path $Dir).Path.Length + 1)
  $exclude | ForEach-Object { if ($relative -like "*$_*") { return $false } }
  return $true
}

Write-Host "Files: $($files.Count)"

# Build form
$form = @{}
foreach ($f in $files) {
  $relative = $f.FullName.Substring((Resolve-Path $Dir).Path.Length + 1)
  $form["files"] = @($form["files"]) + @($f.FullName)
}

try {
  $resp = Invoke-RestMethod -Uri "$Server/api/compile/files" `
    -Method Post `
    -SkipCertificateCheck `
    -Form @{ files = $files }
} catch {
  Write-Error "Failed to submit: $_"
  exit 1
}

$buildId = $resp.build_id
Write-Host "build_id: $buildId" -ForegroundColor Green

if (-not $Wait -and -not $Open) {
  Write-Host ""
  Write-Host "Submitted. Check status:"
  Write-Host "  curl -sk '$Server/api/build/$buildId/status'"
  Write-Host "Flash page:"
  Write-Host "  $Server/?build_id=$buildId"
  exit 0
}

# Poll
if ($Wait) {
  Write-Host "Waiting..."
  while ($true) {
    try {
      $status = Invoke-RestMethod -Uri "$Server/api/build/$buildId/status" `
        -SkipCertificateCheck
    } catch {
      Start-Sleep -Seconds 2
      continue
    }

    if ($status.status -eq "done") {
      Write-Host "✅ Compile complete — $($status.bin_size) bytes, $($status.elapsed)s" -ForegroundColor Green
      break
    } elseif ($status.status -eq "error") {
      Write-Host "❌ Compile failed: $($status.error)" -ForegroundColor Red
      exit 1
    }
    Start-Sleep -Seconds 2
  }
}

if ($Open) {
  $flashUrl = "$Server/?build_id=$buildId"
  Write-Host "Opening: $flashUrl"
  Start-Process $flashUrl
}
