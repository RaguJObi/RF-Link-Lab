param([string]$UbuntuAddress = 'ubuntu-bench.local')
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$auditDir = Join-Path $projectRoot ('build/hardware-start-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $auditDir | Out-Null
$statusPath = Join-Path $auditDir 'status.json'
$target = 'rfll@' + $UbuntuAddress
function Write-BenchStatus([string]$phase, [int]$code) {
    [ordered]@{phase=$phase;target=$target;exit_code=$code;updated_utc=[DateTime]::UtcNow.ToString('o')} |
        ConvertTo-Json | Set-Content -LiteralPath $statusPath -Encoding utf8
}
try {
    Write-BenchStatus 'waiting_for_terminal_login' 0
    Write-Host 'RF Link Lab - real Nucleo setup and UART' -ForegroundColor Cyan
    Write-Host 'Enter SSH and sudo passwords only at their terminal prompts. They are not saved.'
    Write-Host 'Keep both boards connected to Ubuntu; press the blue B1 button when prompted.'
    $scriptFile = Join-Path $PSScriptRoot 'START-PHYSICAL-BENCH.sh'
    $scriptText = [IO.File]::ReadAllText($scriptFile).Replace("`r`n", "`n")
    $payload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($scriptText))
    $remoteFile = '/home/rfll/SDR/bladeRF/RF-Link-Lab/build/physical-bench-' + [guid]::NewGuid().ToString('N') + '.sh'
    # Script contents enter as base64 in a command argument. SSH stdin remains
    # the user's terminal so remote sudo can securely read its TTY prompt.
    $remoteCommand = 'umask 077; mkdir -p /home/rfll/SDR/bladeRF/RF-Link-Lab/build; printf %s ' + $payload + ' | base64 --decode > ' + $remoteFile + ' && bash ' + $remoteFile
    Write-BenchStatus 'remote_session' 0
    & ssh -tt -o ConnectTimeout=10 -o StrictHostKeyChecking=yes $target $remoteCommand |
        Tee-Object -FilePath (Join-Path $auditDir 'ubuntu-console.txt') | ForEach-Object { Write-Host $_ }
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        Write-BenchStatus 'needs_inspection' $code
        Write-Host "Procedure stopped with code $code; evidence remains in the printed session directory." -ForegroundColor Yellow
        exit $code
    }
    Write-BenchStatus 'uart_procedure_complete' 0
    Write-Host 'Procedure finished. Check the saved observations before calling the board validated.' -ForegroundColor Green
} catch {
    Write-BenchStatus 'failed' 1
    $_ | Out-String | Set-Content -LiteralPath (Join-Path $auditDir 'error.txt')
    throw
}
