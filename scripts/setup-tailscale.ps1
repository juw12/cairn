# Cairn: reach this PC from your phone on any network, through Tailscale.
#
#   1. installs Tailscale if it's missing (Windows asks for permission once)
#   2. lets Tailscale devices (100.64.0.0/10 only) reach Cairn's phone port through Windows Firewall
#   3. signs this PC in and turns on "Run unattended", so Tailscale stays on after reboots and sign-outs
#
# Started from Cairn: Settings > Phone > Set up Tailscale. Safe to run again.

param([int]$Port = 47778)

$ErrorActionPreference = "Continue"
$exe = "C:\Program Files\Tailscale\tailscale.exe"
$rule = "Cairn phone access (Tailscale)"

function Step($text) {
    Write-Host ""
    Write-Host $text -ForegroundColor Cyan
}

function Done($text, $code) {
    Write-Host ""
    Write-Host $text -ForegroundColor $(if ($code -eq 0) { "Green" } else { "Yellow" })
    Read-Host "Press Enter to close"
    exit $code
}

Write-Host "Cairn: access from anywhere (Tailscale)" -ForegroundColor White

Step "1/3  Tailscale on this PC"
if (Test-Path $exe) {
    Write-Host "Already installed."
} else {
    Write-Host "Installing... Windows will ask for permission."
    winget install --id Tailscale.Tailscale --exact --silent --accept-source-agreements --accept-package-agreements
    if (-not (Test-Path $exe)) {
        Done "Tailscale didn't install (no permission, or blocked by IT). Get it from https://tailscale.com/download/windows, then run this again from Cairn." 1
    }
}

Step "2/3  Let Tailscale devices reach Cairn (port $Port)"
$exists = Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue
if ($exists) {
    Write-Host "Firewall rule already there."
} else {
    Write-Host "Windows will ask for permission."
    $add = "New-NetFirewallRule -DisplayName '$rule' -Direction Inbound -Protocol TCP -LocalPort $Port -RemoteAddress 100.64.0.0/10 -Action Allow -Profile Any | Out-Null"
    try {
        Start-Process powershell.exe -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList "-NoProfile", "-Command", $add
    } catch {
        Write-Host "Skipped (no permission). If your phone can't connect, allow Python in Windows Firewall." -ForegroundColor Yellow
    }
    if (Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue) {
        Write-Host "Added."
    }
}

Step "3/3  Sign this PC in to Tailscale (use the same account as your phone)"
$state = (& $exe status --json 2>$null | ConvertFrom-Json).BackendState
if ($state -ne "Running") {
    Write-Host "A sign-in link opens in your browser (or Ctrl+click the link below)."
    & $exe up --unattended
} else {
    & $exe up --unattended 2>$null | Out-Null
    Write-Host "Already signed in. Kept on with 'Run unattended'."
}

$status = & $exe status --json 2>$null | ConvertFrom-Json
if ($status.BackendState -eq "Running") {
    $name = $status.Self.DNSName.TrimEnd(".")
    Done "Done. This PC is $name on your tailnet. In Cairn: Settings > Pair a phone, scan the new QR code, and keep Tailscale on in your phone." 0
}
Done "Not signed in yet. Open Tailscale from the tray, sign in, then pair again in Cairn." 1
