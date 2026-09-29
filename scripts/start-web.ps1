$ErrorActionPreference = "Stop"
Set-Location (Join-Path (Split-Path $PSScriptRoot -Parent) "web")
& npm.cmd run dev -- --hostname 127.0.0.1 --port 3000
