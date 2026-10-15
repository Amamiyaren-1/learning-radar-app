# learning-radar 命令封装：解决本机 python 不在 PATH 的问题。
# Resolve LEARNRADAR_PYTHON, then working PATH Python; skip Store aliases.
#   .\scripts\lr.ps1 verify-models
#   .\scripts\lr.ps1 run --topic tool-calling --candidate owner/name
#
# Resolve LEARNRADAR_PYTHON, then working PATH Python; skip Store aliases.
# Resolve LEARNRADAR_PYTHON, then working PATH Python; skip Store aliases.

param(
    [switch]$DemoLauncher,
    [switch]$RuntimeLauncher,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

function Test-PythonInterpreter {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    try {
        $version = & $Path -c "import sys; print(sys.version_info[0])" 2>$null
        return ($LASTEXITCODE -eq 0 -and "$version".Trim() -eq "3")
    } catch {
        return $false
    }
}

function Resolve-PythonInterpreter {
    $tries = New-Object System.Collections.Generic.List[string]
    if ($env:LEARNRADAR_PYTHON) { $tries.Add($env:LEARNRADAR_PYTHON) }
    foreach ($name in @("python", "python3")) {
        $found = Get-Command $name -ErrorAction SilentlyContinue
        if ($found) { $tries.Add($found.Source) }
    }
    foreach ($candidate in $tries) {
        if (Test-PythonInterpreter -Path $candidate) { return $candidate }
    }
    return $null
}

$python = Resolve-PythonInterpreter
if (-not $python) {
    throw "找不到可用的 Python 3 解释器；请设置环境变量 LEARNRADAR_PYTHON 指向 python.exe"
}

Push-Location $repoRoot
try {
    $module = if ($RuntimeLauncher) { "learnradar.runtime_launcher" } elseif ($DemoLauncher) { "learnradar.demo_launcher" } else { "learnradar.cli" }
    & $python -m $module @Args
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $code
