# 仅启动隔离离线演示；网页、worker 在本脚本返回/浏览器关闭后继续运行。
param(
    [ValidateSet("start", "stop", "status")][string]$Action = "start",
    [string]$DataDir = "",
    [switch]$OpenBrowser
)
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $DataDir) { $DataDir = Join-Path $repoRoot "data\demo-2-1" }
$launcherArgs = @($Action, "--data-dir", $DataDir)
if ($OpenBrowser) { $launcherArgs += "--open-browser" }
# Reuse interpreter discovery without changing the normal CLI entry.
& (Join-Path $PSScriptRoot "lr.ps1") -DemoLauncher @launcherArgs
exit $LASTEXITCODE
