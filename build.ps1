<#
.SYNOPSIS
  Builds the doshy OS ISO from Windows.

  Uses Docker Desktop if it is available, otherwise a Debian distribution inside WSL
  (installing one automatically if none exists). The finished ISO is copied to .\out\.

.EXAMPLE
  .\build.ps1
  .\build.ps1 -Locale en_GB.UTF-8 -Keyboard gb -Timezone Europe/London
#>
param(
    [string]$OsName   = "doshyos-OG",
    [string]$Locale   = "en_US.UTF-8",
    [string]$Keyboard = "us",
    [string]$Timezone = "Etc/UTC",
    [ValidateSet("auto", "docker", "wsl")]
    [string]$Backend  = "auto"
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
New-Item -ItemType Directory -Force -Path (Join-Path $root "out") | Out-Null

function Test-Docker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { return $false }
    docker info *> $null
    return ($LASTEXITCODE -eq 0)
}

if ($Backend -eq "auto") {
    $Backend = if (Test-Docker) { "docker" } else { "wsl" }
}

Write-Host "==> Backend: $Backend"

if ($Backend -eq "docker") {
    docker volume create doshy-build | Out-Null
    docker run --rm --privileged `
        -e OS_NAME=$OsName -e LIVE_LOCALE=$Locale -e LIVE_KEYBOARD=$Keyboard -e LIVE_TIMEZONE=$Timezone `
        -v "${root}:/src" -v doshy-build:/build `
        debian:bookworm bash -c "sed 's/\r`$//' /src/scripts/docker-build.sh | bash"
    if ($LASTEXITCODE -ne 0) { throw "Docker build failed" }
}
else {
    $status = (wsl --status 2>&1 | ForEach-Object { "$_" -replace "`0", "" }) -join "`n"
    if ($status -match "not supported|Virtual Machine Platform|is not installed") {
        Write-Host ""
        Write-Host "WSL 2 is not ready on this PC. One-off setup (needs admin + a reboot):" -ForegroundColor Yellow
        Write-Host "  1. Open PowerShell *as Administrator* and run:   wsl --install --no-distribution"
        Write-Host "  2. Reboot. (If it still complains, enable 'Virtualization'/'SVM'/'VT-x' in the BIOS.)"
        Write-Host "  3. Run .\build.ps1 again."
        Write-Host ""
        Write-Host "No admin rights? Push this folder to GitHub instead - .github/workflows/build-iso.yml builds the ISO for you."
        throw "WSL 2 not available"
    }

    $distro = "Debian"
    $installed = (wsl --list --quiet 2>$null | ForEach-Object { $_ -replace "`0", "" } | Where-Object { $_ -match '^(Debian|Ubuntu)' } | Select-Object -First 1)
    if (-not $installed) {
        Write-Host "==> No Debian/Ubuntu found in WSL - installing Debian (one-off)"
        wsl --install -d Debian --no-launch
        if ($LASTEXITCODE -ne 0) { throw "wsl --install failed. Enable WSL (wsl --install) and reboot, then re-run." }
        # First launch as root, no user prompt
        wsl -d Debian -u root -- true
    } else {
        $distro = $installed.Trim()
    }

    $linuxPath = (wsl -d $distro -u root --exec wslpath -a $root)
    if ($LASTEXITCODE -ne 0 -or -not $linuxPath) { throw "Could not resolve the project path in WSL" }
    $linuxPath = $linuxPath.Trim()
    Write-Host "==> Building in WSL ($distro) from $linuxPath"
    # Pass the path as an argument, never interpolate it into shell source.
    $buildCommand = 'cd "$1" && sed ''s/\r$//'' build.sh > /tmp/doshy-build.sh && SRC_DIR="$1" bash /tmp/doshy-build.sh'
    wsl -d $distro -u root --exec env "OS_NAME=$OsName" "LIVE_LOCALE=$Locale" "LIVE_KEYBOARD=$Keyboard" "LIVE_TIMEZONE=$Timezone" `
        bash -c $buildCommand doshy-build $linuxPath
    if ($LASTEXITCODE -ne 0) { throw "WSL build failed" }
}

Write-Host ""
Write-Host "==> ISO(s) in $(Join-Path $root 'out'):"
Get-ChildItem (Join-Path $root "out") -Filter *.iso | ForEach-Object { Write-Host ("    {0}  ({1:N0} MB)" -f $_.Name, ($_.Length / 1MB)) }
