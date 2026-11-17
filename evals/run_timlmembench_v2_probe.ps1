param([ValidateSet('write','activation')][string]$Kind = 'write')

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = 'D:\anaconda3\envs\kaggle_nli\python.exe'
$benchmark = 'E:\2026-1\tmp\TimlMemBench'
$tables = Join-Path $repo 'results\tables'
$facts = Join-Path $tables 'timlmembench_product_20261114_dev_rerun_facts.json'
$reference = Join-Path $tables "timlmembench_v2_failed_${Kind}_reference.jsonl"
$prediction = Join-Path $tables "timlmembench_v2_failed_${Kind}_predictions.jsonl"
$score = Join-Path $tables "timlmembench_v2_failed_${Kind}_scores.jsonl"
$family = if ($Kind -eq 'activation') { 'activation' } else { 'governance' }
$caseType = if ($Kind -eq 'activation') { 'activation_appropriateness' } else { 'write_op_accuracy' }
$count = if ($Kind -eq 'activation') { 14 } else { 5 }
$baseline = "whale_core_v2_failed_${Kind}"
$adapter = "$python $repo\evals\timlmembench_governed.py --max-queries $count --max-calls 500 --max-facts-per-session 6 --facts-in $facts"

$oldEncoding = $env:PYTHONIOENCODING
$oldPythonPath = $env:PYTHONPATH
$hadKey = [bool]$env:AGENTSCOPE_API_KEY
if (-not $hadKey) {
    $env:AGENTSCOPE_API_KEY = (Get-Content -LiteralPath 'E:\2026-1\tmp\agentscope_api_key.txt' -Raw).Trim()
}
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = "$repo\src;$repo\plugins;$repo"
try {
    & $python (Join-Path $repo 'evals\timlmembench_targeted.py') `
        --official-runner (Join-Path $benchmark 'code\run_baseline_predictions.py') `
        --lang zh --split dev --case-family $family --case-types $caseType `
        --failed-from $reference --baseline $baseline `
        --adapter-command $adapter --output $prediction
    if ($LASTEXITCODE -ne 0) { throw 'prediction run failed' }
    & $python (Join-Path $repo 'evals\timlmembench_judge_resume.py') `
        --benchmark-root $benchmark --predictions $prediction --output $score `
        --provider agentscope --model deepseek-v4.1-flash-1017 --max-judges $count
    if ($LASTEXITCODE -ne 0) { throw 'judge run failed' }
    Write-Output "completed ${Kind}: $prediction | $score"
} finally {
    if (-not $hadKey) { Remove-Item Env:\AGENTSCOPE_API_KEY -ErrorAction SilentlyContinue }
    if ($null -eq $oldEncoding) {
        Remove-Item Env:\PYTHONIOENCODING -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONIOENCODING = $oldEncoding
    }
    if ($null -eq $oldPythonPath) {
        Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONPATH = $oldPythonPath
    }
}
