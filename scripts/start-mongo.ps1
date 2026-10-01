$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot
$executable = Get-ChildItem -LiteralPath (Join-Path $projectRoot '.runtime/tools/mongodb') -Recurse -Filter mongod.exe | Select-Object -First 1
if (-not $executable) { throw 'Install official MongoDB Windows ZIP under .runtime/tools/mongodb first. See docs/persistence.md.' }
New-Item -ItemType Directory -Force -Path '.runtime/mongo/data' | Out-Null
& $executable.FullName --dbpath .runtime/mongo/data --bind_ip 127.0.0.1 --port 27017 --logpath .runtime/mongo/mongod.log --logappend
