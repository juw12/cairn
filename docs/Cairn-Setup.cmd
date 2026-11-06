@echo off
title Cairn setup
echo Installing Cairn (no admin needed). This takes a minute the first time...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $c=New-Object Net.WebClient; $c.Proxy=[Net.WebRequest]::GetSystemWebProxy(); $c.Proxy.Credentials=[Net.CredentialCache]::DefaultNetworkCredentials; iex $c.DownloadString('https://juw12.github.io/cairn/install.ps1')"
if errorlevel 1 (
  echo.
  echo Setup didn't finish. The message above says why.
  pause
  exit /b 1
)
timeout /t 5 >nul
