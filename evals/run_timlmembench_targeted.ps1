param(
    [ValidateSet('pilot', 'full', 'failed')]
    [string] $Stage = 'pilot'
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$benchmark = 'E:\2026-1\tmp\TimlMemBench'
$python = 'D:\anaconda3\python.exe'
$count = if ($Stage -eq 'pilot') { 2 } elseif ($Stage -eq 'failed') { 12 } else { 18 }
$facts = if ($Stage -eq 'failed') {
    Join-Path 'E:\2026-1\tmp' 'timlmembench_targeted_full_facts.json'
} else {
    Join-Path 'E:\2026-1\tmp' "timlmembench_targeted_${Stage}_facts.json"
}
$tables = Join-Path $repo 'results\tables'

if (-not (Test-Path -LiteralPath $benchmark)) {
    throw "Official benchmark clone is missing: $benchmark"
}

$secure = Read-Host 'AgentScope API key (input hidden)' -AsSecureString
$env:AGENTSCOPE_API_KEY = [System.Net.NetworkCredential]::new('', $secure).Password
if (-not $env:AGENTSCOPE_API_KEY) { throw 'AgentScope API key is empty' }
$previousPythonIoEncoding = $env:PYTHONIOENCODING
$env:PYTHONIOENCODING = 'utf-8'

try {
    $conditions = if ($Stage -eq 'failed') { @('governed') } else {
        @('baseline', 'source', 'governed')
    }
    foreach ($condition in $conditions) {
        $prediction = Join-Path $tables "timlmembench_targeted_${Stage}_${condition}_predictions.jsonl"
        $score = Join-Path $tables "timlmembench_targeted_${Stage}_${condition}_scores.jsonl"
        $adapter = "$python $repo\evals\timlmembench_governed.py --max-queries $count --max-calls 150"
        if ($condition -eq 'baseline') {
            $adapter += " --disable-governance --disable-source-context --facts-out $facts"
        } else {
            $adapter += " --facts-in $facts"
            if ($condition -eq 'source') { $adapter += ' --disable-governance' }
        }
        Write-Host "Running $Stage / $condition predictions ($count cases max)..."
        $selection = if ($Stage -eq 'failed') {
            @('--failed-from', (Join-Path $tables 'timlmembench_targeted_full_governed_scores.jsonl'))
        } else { @() }
        & $python (Join-Path $repo 'evals\timlmembench_targeted.py') `
            --official-runner (Join-Path $benchmark 'code\run_baseline_predictions.py') `
            --lang zh --split dev `
            --case-types write_op_accuracy provenance_recoverability `
            @selection `
            --limit $count `
            --baseline "whale_core_targeted_${Stage}_${condition}" `
            --adapter-command $adapter `
            --output $prediction
        if ($LASTEXITCODE -ne 0) { throw "Prediction failed: $condition" }
        Write-Host "Scoring $Stage / $condition..."
        & $python (Join-Path $repo 'evals\timlmembench_judge_resume.py') `
            --benchmark-root $benchmark --predictions $prediction --output $score `
            --provider agentscope --model deepseek-v4.1-flash-1017 `
            --max-judges $count
        if ($LASTEXITCODE -ne 0) { throw "Scoring failed: $condition" }
    }
} finally {
    Remove-Item Env:\AGENTSCOPE_API_KEY -ErrorAction SilentlyContinue
    if ($null -eq $previousPythonIoEncoding) {
        Remove-Item Env:\PYTHONIOENCODING -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONIOENCODING = $previousPythonIoEncoding
    }
    $secure.Dispose()
}
