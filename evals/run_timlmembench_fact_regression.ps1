param(
    [ValidateSet('failed', 'full', 'write')]
    [string] $Stage = 'failed',
    [string] $KeyFile = '',
    [string] $RunTag = 'factservice_v3',
    [string] $ReferenceScores = '',
    [int] $Limit = 0,
    [int] $MaxFacts = 6,
    [switch] $RefreshFacts,
    [string] $FactsFile = ''
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$benchmark = 'E:\2026-1\tmp\TimlMemBench'
$facts = if ($FactsFile) { $FactsFile } else {
    'E:\2026-1\tmp\timlmembench_targeted_full_facts.json'
}
$python = 'D:\anaconda3\envs\kaggle_nli\python.exe'
$tables = Join-Path $repo 'results\tables'
$oldScores = if ($ReferenceScores) { $ReferenceScores } else {
    Join-Path $tables 'timlmembench_targeted_v5_full_scores.jsonl'
}
$prefix = Join-Path $tables "timlmembench_${RunTag}_$Stage"
$prediction = "${prefix}_predictions.jsonl"
$score = "${prefix}_scores.jsonl"

foreach ($path in @($benchmark, $oldScores, $python)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing input: $path" }
}
if (-not $RefreshFacts -and -not (Test-Path -LiteralPath $facts)) {
    throw "Missing frozen facts: $facts"
}

$secure = $null
if ($KeyFile) {
    $env:AGENTSCOPE_API_KEY = (Get-Content -LiteralPath $KeyFile -Raw).Trim()
} elseif (-not $env:AGENTSCOPE_API_KEY) {
    $secure = Read-Host 'AgentScope API key (input hidden)' -AsSecureString
    $env:AGENTSCOPE_API_KEY = [System.Net.NetworkCredential]::new('', $secure).Password
}
if (-not $env:AGENTSCOPE_API_KEY) { throw 'AgentScope API key is empty' }
$previousEncoding = $env:PYTHONIOENCODING
$env:PYTHONIOENCODING = 'utf-8'

try {
    $selection = if ($Stage -eq 'failed') { @('--failed-from', $oldScores) } else { @() }
    $caseTypes = if ($Stage -eq 'write') { @('write_op_accuracy') } else {
        @('write_op_accuracy', 'provenance_recoverability')
    }
    $factMode = if ($RefreshFacts) { '--facts-out' } else { '--facts-in' }
    $maxCalls = if ($RefreshFacts) { 180 } else { 120 }
    $adapter = "$python $repo\evals\timlmembench_governed.py --max-queries 18 --max-calls $maxCalls --max-facts-per-session $MaxFacts $factMode $facts"
    $predictionArgs = @(
        '--official-runner', (Join-Path $benchmark 'code\run_baseline_predictions.py'),
        '--lang', 'zh', '--split', 'dev', '--case-types'
    ) + $caseTypes + $selection + @(
        '--baseline', "whale_core_${RunTag}_$Stage",
        '--adapter-command', $adapter, '--output', $prediction
    )
    if ($Limit -gt 0) { $predictionArgs += @('--limit', [string]$Limit) }
    & $python (Join-Path $repo 'evals\timlmembench_targeted.py') @predictionArgs
    if ($LASTEXITCODE -ne 0) { throw 'Prediction failed' }

    & $python (Join-Path $repo 'evals\timlmembench_judge_resume.py') `
        --benchmark-root $benchmark --predictions $prediction --output $score `
        --provider agentscope --model deepseek-v4.1-flash-1017 --max-judges 18
    if ($LASTEXITCODE -ne 0) { throw 'Scoring failed' }

    & $python (Join-Path $repo 'evals\compare_timlmembench_runs.py') `
        --before $oldScores --after $score
    if ($LASTEXITCODE -ne 0) { throw 'Comparison failed' }
} finally {
    Remove-Item Env:\AGENTSCOPE_API_KEY -ErrorAction SilentlyContinue
    if ($null -eq $previousEncoding) {
        Remove-Item Env:\PYTHONIOENCODING -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONIOENCODING = $previousEncoding
    }
    if ($secure) { $secure.Dispose() }
}
