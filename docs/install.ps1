# Cairn for Windows: one-click install / update. No admin rights needed.
#
#   Double-click Cairn-Setup.cmd (from https://juw12.github.io/cairn/), or in PowerShell:
#   irm https://juw12.github.io/cairn/install.ps1 | iex
#
# What it does, skipping anything already there:
#   1. Python 3.13+ (per-user, via winget or python.org)   2. Git (winget, or a portable MinGit)
#   3. Cairn itself into %LOCALAPPDATA%\Programs\Cairn\app (a git checkout, so it can update itself)
#   4. The window + tray libraries (pywebview, pystray, pillow)
#   5. A Start menu entry, then opens Cairn. Cairn starts with Windows from then on (switch it off
#      in Cairn's settings) and keeps itself up to date.

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Repo = "https://github.com/juw12/cairn.git"
$Root = Join-Path $env:LOCALAPPDATA "Programs\Cairn"
$App = Join-Path $Root "app"
$StartMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Cairn.lnk"

function Say($text) { Write-Host "  $text" }
function Step($text) { Write-Host ""; Write-Host "> $text" -ForegroundColor Cyan }

# Corporate networks: let pip and git use the same proxy Windows uses.
$systemProxy = [Net.WebRequest]::GetSystemWebProxy()
$proxyUri = $systemProxy.GetProxy([Uri]"https://pypi.org")
if ($proxyUri -and $proxyUri.Host -ne "pypi.org" -and -not $env:HTTPS_PROXY) {
    $env:HTTPS_PROXY = $proxyUri.AbsoluteUri.TrimEnd("/")
    $env:HTTP_PROXY = $env:HTTPS_PROXY
    Say "Using your network proxy $env:HTTPS_PROXY"
}

function Get-WebFile($url, $path) {
    $client = New-Object Net.WebClient
    $client.Proxy = $systemProxy
    $client.Proxy.Credentials = [Net.CredentialCache]::DefaultNetworkCredentials
    $client.Headers.Add("User-Agent", "cairn-installer")
    $client.DownloadFile($url, $path)
}

function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Has-Winget { return [bool](Get-Command winget -ErrorAction SilentlyContinue) }

# ------------------------------------------------------------------ Python
function Find-Python {
    $candidates = @()
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $found = & $py.Source -3 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $found) { $candidates += $found.Trim() }
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python -and $python.Source -notlike "*WindowsApps*") { $candidates += $python.Source }
    $candidates += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe", "$env:ProgramFiles\Python3*\python.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | ForEach-Object { $_.FullName }
    foreach ($candidate in $candidates) {
        $ok = & $candidate -c "import sys; print(int(sys.version_info >= (3, 13)))" 2>$null
        if ($ok -eq "1") { return $candidate }
    }
    return $null
}

Step "Python"
$Python = Find-Python
if (-not $Python) {
    if (Has-Winget) {
        Say "Installing Python 3.13 (just for you, no admin)..."
        winget install -e --id Python.Python.3.13 --scope user --silent --accept-package-agreements --accept-source-agreements | Out-Null
        Refresh-Path
        $Python = Find-Python
    }
    if (-not $Python) {
        Say "Downloading Python from python.org..."
        $setup = Join-Path $env:TEMP "python-3.13.17-amd64.exe"
        Get-WebFile "https://www.python.org/ftp/python/3.13.17/python-3.13.17-amd64.exe" $setup
        Start-Process $setup -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_test=0" -Wait
        Refresh-Path
        $Python = Find-Python
    }
    if (-not $Python) { throw "Couldn't install Python. Install it from https://www.python.org and run this again." }
}
$Pythonw = Join-Path (Split-Path $Python) "pythonw.exe"
Say "OK: $Python"

# ------------------------------------------------------------------ Git
function Find-Git {
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) { return $git.Source }
    $hit = Get-ChildItem "$env:LOCALAPPDATA\Programs\Git\cmd\git.exe", "$env:ProgramFiles\Git\cmd\git.exe", "$Root\git\cmd\git.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($hit) { return $hit.FullName }
    return $null
}

Step "Git (keeps your notes in sync and Cairn up to date)"
$Git = Find-Git
if (-not $Git -and (Has-Winget)) {
    Say "Installing Git..."
    winget install -e --id Git.Git --scope user --silent --accept-package-agreements --accept-source-agreements | Out-Null
    Refresh-Path
    $Git = Find-Git
}
if (-not $Git) {
    Say "Downloading a portable Git..."
    $release = Invoke-RestMethod "https://api.github.com/repos/git-for-windows/git/releases/latest" -Headers @{ "User-Agent" = "cairn-installer" }
    $asset = $release.assets | Where-Object { $_.name -match "^MinGit-[\d.]+-64-bit\.zip$" } | Select-Object -First 1
    $zip = Join-Path $env:TEMP $asset.name
    Get-WebFile $asset.browser_download_url $zip
    Expand-Archive $zip -DestinationPath (Join-Path $Root "git") -Force
    $Git = Join-Path $Root "git\cmd\git.exe"
}
if (-not (Test-Path $Git)) { throw "Couldn't install Git. Install it from https://git-scm.com and run this again." }
$gitDir = Split-Path $Git
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not (($userPath -split ";") -contains $gitDir) -and -not (Get-Command git -ErrorAction SilentlyContinue)) {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$gitDir", "User")
}
$env:Path = "$gitDir;$env:Path"
Say "OK: $Git"

# ------------------------------------------------------------------ Cairn
Step "Cairn"
New-Item -ItemType Directory -Force -Path $Root | Out-Null
# A running Cairn keeps files open; it restarts at the end.
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "Cairn\.pyw|cairn_app\.pyw|cairn desktop" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
if (Test-Path (Join-Path $App ".git")) {
    & $Git -C $App pull --ff-only --quiet
    Say "Updated to the latest version"
} else {
    if (Test-Path $App) { Remove-Item $App -Recurse -Force }
    & $Git clone --quiet $Repo $App
    if ($LASTEXITCODE -ne 0) { throw "Couldn't download Cairn from GitHub." }
    Say "Downloaded"
}
# Leftovers from the early hand-made setup.
foreach ($old in "cairn_app.pyw", "Cairn.ps1", "cairn.ico", "deps.stamp", "tray-hint.shown") {
    Remove-Item (Join-Path $Root $old) -Force -ErrorAction SilentlyContinue
}

Step "Window and tray libraries"
& $Python -m pip install --user --quiet --disable-pip-version-check --upgrade pywebview pystray pillow
if ($LASTEXITCODE -ne 0) { throw "Couldn't install pywebview/pystray (pip failed)." }
Say "OK"

Step "Start menu"
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($StartMenu)
$link.TargetPath = $Pythonw
$link.Arguments = "`"$App\Cairn.pyw`""
$link.WorkingDirectory = $App
$link.IconLocation = "$App\docs\icons\cairn.ico"
$link.Description = "Cairn: your second brain"
$link.Save()
Say "Added Cairn to the Start menu"

Step "Opening Cairn"
Start-Process $Pythonw -ArgumentList "`"$App\Cairn.pyw`"" -WorkingDirectory $App
Write-Host ""
Write-Host "Done. Cairn is open, lives in the tray when you close it, and starts with Windows." -ForegroundColor Green
Write-Host "Everything else (opening your notes, syncing, settings) is inside the app."
