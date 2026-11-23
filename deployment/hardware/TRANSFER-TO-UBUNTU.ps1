param(
    [string]$UbuntuAddress = 'ubuntu-bench.local',
    [string]$UbuntuUser = 'rfll'
)
$ErrorActionPreference = 'Stop'
$kitRoot = $PSScriptRoot
$remoteProject = '/home/rfll/SDR/bladeRF/RF-Link-Lab'
$target = $UbuntuUser + '@' + $UbuntuAddress
$statusPath = Join-Path $kitRoot 'transfer-status.json'
function Invoke-UbuntuScript([string]$remoteScript) {
    $scriptBytes = [Text.Encoding]::UTF8.GetBytes($remoteScript.Replace("`r`n", "`n"))
    $encodedScript = [Convert]::ToBase64String($scriptBytes)
    # Send script text over stdin. Only a constant command enters SSH's shell
    # parser; encoding also avoids PowerShell/SSH nested-quote differences.
    $encodedScript | & ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=yes $target 'base64 --decode --ignore-garbage | bash'
    $script:lastRemoteExit = $LASTEXITCODE
}
function Write-TransferStatus([string]$phase, [string]$detail) {
    [ordered]@{phase=$phase; detail=$detail; target=$target; remote_project=$remoteProject;
        updated_utc=[DateTime]::UtcNow.ToString('o')} |
        ConvertTo-Json | Set-Content -LiteralPath $statusPath -Encoding UTF8
}
try {
    Write-Host 'RF Link Lab hardware transfer' -ForegroundColor Cyan
    Write-Host 'Enter the Ubuntu login password only at the SSH/SCP prompts in this window.'
    Write-Host 'The password is not requested or saved by this script.'
    # Read the colocated release metadata rather than selecting an older
    # runtime by a version embedded in this helper. Validate before any SSH.
    $manifestPath = Join-Path $kitRoot 'KIT-MANIFEST.json'
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $files = @($manifest.files)
    if ($files.Count -eq 0) { throw 'The transfer kit has no file inventory.' }
    $seenNames = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($entry in $files) {
        if ($entry.file -isnot [string] -or $entry.file -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._+-]*$' -or
            $entry.file -in @('.', '..') -or -not $seenNames.Add($entry.file)) {
            throw 'The transfer kit contains an unsafe or duplicate inventory filename.'
        }
        if ($entry.sha256 -isnot [string] -or $entry.sha256 -cnotmatch '^[0-9a-f]{64}$' -or
            ($entry.bytes -isnot [long] -and $entry.bytes -isnot [int]) -or $entry.bytes -lt 0) {
            throw 'The transfer kit contains invalid file hash or size metadata.'
        }
        $localFile = Join-Path $kitRoot $entry.file
        $item = Get-Item -LiteralPath $localFile
        if ($item.PSIsContainer -or $item.Length -ne $entry.bytes -or
            (Get-FileHash -LiteralPath $localFile -Algorithm SHA256).Hash.ToLowerInvariant() -cne $entry.sha256) {
            throw ('Transfer kit file hash or size mismatch: ' + $entry.file)
        }
    }
    $validations = @($files | Where-Object { $_.file -cmatch '^UBUNTU-HARDWARE-VALIDATION-v[0-9]+\.[0-9]+\.[0-9]+\.json$' })
    $archives = @($files | Where-Object { $_.file -cmatch '^RF-Link-Lab-v[0-9]+\.[0-9]+\.[0-9]+-Ubuntu22-x86_64\.tar\.gz$' })
    if ($validations.Count -ne 1 -or $archives.Count -ne 1) {
        throw 'The transfer kit must identify exactly one Ubuntu runtime and one Ubuntu validation record.'
    }
    $localArchives = @(Get-ChildItem -LiteralPath $kitRoot -File | Where-Object { $_.Name -clike 'RF-Link-Lab-v*-Ubuntu22-x86_64.tar.gz' })
    $localValidations = @(Get-ChildItem -LiteralPath $kitRoot -File | Where-Object { $_.Name -clike 'UBUNTU-HARDWARE-VALIDATION-v*.json' })
    if ($localArchives.Count -ne 1 -or $localValidations.Count -ne 1 -or
        $localArchives[0].Name -cne $archives[0].file -or $localValidations[0].Name -cne $validations[0].file) {
        throw 'The colocated Ubuntu runtime and validation files are ambiguous or absent from the kit inventory.'
    }
    $validation = Get-Content -LiteralPath (Join-Path $kitRoot $validations[0].file) -Raw | ConvertFrom-Json
    if ($validation.schema -cne 'rf-link-lab-ubuntu-hardware-release-v1' -or
        $validation.version -isnot [string] -or $validation.version -cnotmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
        throw 'Unsupported Ubuntu validation record or release version.'
    }
    $version = $validation.version
    $runtimeDir = 'RF-Link-Lab-v' + $version + '-Ubuntu22-x86_64'
    if ($validations[0].file -cne ('UBUNTU-HARDWARE-VALIDATION-v' + $version + '.json') -or
        $archives[0].file -cne ($runtimeDir + '.tar.gz') -or $validation.archive -cne $archives[0].file -or
        $validation.sha256 -cne $archives[0].sha256 -or $validation.bytes -ne $archives[0].bytes) {
        throw 'Ubuntu runtime and validation metadata identify different releases or archive hashes.'
    }
    if ($manifest.kit -isnot [string]) { throw 'The transfer kit is missing its release-directory identity.' }
    $kitName = ($manifest.kit -split '[\\/]')[-1]
    $expectedKitPattern = '^transfer-v' + [regex]::Escape($version) + '-([0-9]{8})$'
    if ($kitName -cmatch $expectedKitPattern) {
        $stamp = $Matches[1]
    } else {
        throw 'Transfer kit directory identity does not match its Ubuntu release.'
    }
    $remoteKit = '/home/rfll/SDR/bladeRF/RF-Link-Lab-transfer-v' + $version + '-' + $stamp
    Write-Host ('Verified release v' + $version + '; destination ' + $remoteKit)
    Write-TransferStatus 'checking_ssh' 'Waiting for interactive Ubuntu login.'
    $preflight = @'
set -euo pipefail
printf 'HOST: '; hostname
printf 'ARCH: '; uname -m
cat /etc/os-release
if test "$(hostname)" != ubuntu-bench; then
  echo 'Unexpected hostname; transfer stopped for inspection.' >&2
  exit 29
fi
test -d /home/rfll/SDR/bladeRF
if test -e /home/rfll/SDR/bladeRF/RF-Link-Lab; then
  echo 'EXISTING PROJECT: installation refused to preserve files.' >&2
  exit 30
fi
if test -e __REMOTE_KIT__; then
  echo 'EXISTING TRANSFER: inspect it before copying again.' >&2
  exit 31
fi
'@
    $preflight = $preflight.Replace('__REMOTE_KIT__', $remoteKit)
    $sshCheckOutput = Invoke-UbuntuScript $preflight
    $sshCheckExit = $script:lastRemoteExit
    $sshCheckOutput | Tee-Object -FilePath (Join-Path $kitRoot 'ubuntu-ssh-preflight.txt') | Write-Host
    if ($sshCheckExit -ne 0) { throw 'SSH preflight did not pass. See ubuntu-ssh-preflight.txt. Existing folders were preserved.' }
    Write-TransferStatus 'copying' 'Copying full source, Git history and Ubuntu portable application.'
    & scp -r -o ConnectTimeout=10 -o StrictHostKeyChecking=yes $kitRoot ($target + ':' + $remoteKit)
    if ($LASTEXITCODE -ne 0) { throw 'SCP did not finish. Preserve the partial transfer and inspect the error.' }
    Write-TransferStatus 'installing' 'Verifying copied hashes and extracting the new project.'
    $install = @'
set -euo pipefail
bash __REMOTE_KIT__/INSTALL-RF-LINK-LAB.sh
project=/home/rfll/SDR/bladeRF/RF-Link-Lab
app="$project/runtime-ubuntu/__RUNTIME_DIR__"
audit="$project/artifacts/bench/host-preflight-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$audit"
{
  echo '=== Host and permissions ==='
  hostname
  uname -a
  cat /etc/os-release
  id
  echo '=== Existing host tools ==='
  for tool in bladeRF-cli st-info st-flash stdbuf; do command -v "$tool" || true; done
  echo '=== USB devices ==='
  if command -v lsusb >/dev/null; then lsusb; lsusb -t; fi
  echo '=== Serial endpoints ==='
  ls -l /dev/serial/by-id/ 2>/dev/null || true
  echo '=== Application local setup ==='
  bash "$app/START-RF-LINK-LAB.sh" doctor
  echo '=== Serial endpoint listing ==='
  bash "$app/START-RF-LINK-LAB.sh" serial-ports
  echo '=== ST-LINK identity; no flash ==='
  if command -v st-info >/dev/null; then st-info --probe || true; fi
  echo '=== bladeRF identity; no TX ==='
  if command -v bladeRF-cli >/dev/null; then bash "$app/START-RF-LINK-LAB.sh" probe; fi
} 2>&1 | tee "$audit/host-preflight.txt"
printf '\nSAVED_PREFLIGHT=%s\n' "$audit/host-preflight.txt"
'@
    $install = $install.Replace('__REMOTE_KIT__', $remoteKit).Replace('__RUNTIME_DIR__', $runtimeDir)
    $remoteOutput = Invoke-UbuntuScript $install
    $remoteExit = $script:lastRemoteExit
    $remoteOutput | Tee-Object -FilePath (Join-Path $kitRoot 'ubuntu-preflight.txt') | Write-Host
    if ($remoteExit -ne 0) { throw 'Remote installation or setup check failed; inspect ubuntu-preflight.txt.' }
    Write-TransferStatus 'complete' 'Project copied and installed; host/device read-only observations saved in ubuntu-preflight.txt.'
    Write-Host 'Transfer and installation finished. No firmware was flashed and no TX was started.' -ForegroundColor Green
} catch {
    Write-TransferStatus 'failed' $_.Exception.Message
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
