# Cairn Pro license server: set its secrets on Cloudflare (run from this folder).
#   .\set-secrets.ps1            asks for the PayPal client id and secret
#   .\set-secrets.ps1 -Signing   also uploads ~/.cairn/seller/signing-key.json as SIGNING_KEY
# Needs Node (npx wrangler) and `wrangler login` once. Behind a proxy set HTTPS_PROXY first.
param([switch]$Signing)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Put-Secret($Name, $Value) {
    $Value | npx --yes wrangler secret put $Name
    if ($LASTEXITCODE -ne 0) { throw "Could not set $Name" }
}

if ($Signing) {
    $keyFile = Join-Path $HOME ".cairn\seller\signing-key.json"
    $raw = Get-Content $keyFile -Raw | ConvertFrom-Json
    $compact = @{ n = "$($raw.n)"; e = "$($raw.e)"; d = "$($raw.d)" } | ConvertTo-Json -Compress
    Put-Secret "SIGNING_KEY" $compact
}

$clientId = Read-Host "PayPal REST app Client ID"
$secret = Read-Host "PayPal REST app Secret" -AsSecureString
$plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret))
Put-Secret "PAYPAL_CLIENT_ID" $clientId
Put-Secret "PAYPAL_SECRET" $plain
Write-Host "Done. Check: https://cairn-pro.juwenmondial.workers.dev/health should say ready: true"
