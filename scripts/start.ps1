# Daily formal entry. Own web/worker only; no task submission or mail retry.
param(
    [ValidateSet('start','status','stop')][string]$Action='start',
    [string]$ConfigRoot='',
    [int]$Port=0,
    [switch]$WebOnly,
    [switch]$OpenBrowser,
    [string[]]$AllowTask=@()
)
$ErrorActionPreference='Stop'
if (-not $ConfigRoot) { $ConfigRoot=Split-Path -Parent $PSScriptRoot }
$runtimeArgs=@($Action,'--config-root',$ConfigRoot,'--port',"$Port")
if ($WebOnly) { $runtimeArgs+='--web-only' }
if ($OpenBrowser) { $runtimeArgs+='--open-browser' }
foreach ($identifier in $AllowTask) { $runtimeArgs+=@('--allow-task',$identifier) }
& (Join-Path $PSScriptRoot 'lr.ps1') -RuntimeLauncher @runtimeArgs
exit $LASTEXITCODE
