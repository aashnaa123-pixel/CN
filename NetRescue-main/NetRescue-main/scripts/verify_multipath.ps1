# scripts/verify_multipath.ps1
#
# Runs the Feature B acceptance experiments against a *running* API, over HTTP,
# using a real file read from disk — no fixtures, no in-process shortcuts. This
# is the path a browser takes.
#
#   .\scripts\verify_multipath.ps1                       # 127.0.0.1:8000
#   .\scripts\verify_multipath.ps1 -Base http://127.0.0.1:8001

param([string]$Base = "http://127.0.0.1:8000")

$ErrorActionPreference = "Stop"
$Headers = @{ "X-NetRescue-Principal" = "verify"; "X-NetRescue-Role" = "operator" }
$script:Failures = 0

function Call($method, $path, $body) {
    $params = @{ Method = $method; Uri = "$Base$path"; Headers = $Headers; TimeoutSec = 300 }
    if ($null -ne $body) {
        $params.Body = ($body | ConvertTo-Json -Depth 12 -Compress)
        $params.ContentType = "application/json"
    }
    try {
        $r = Invoke-RestMethod @params
        return @{ ok = $true; data = $r }
    } catch {
        $status = 0; $detail = $null
        if ($_.Exception.Response) {
            $status = [int]$_.Exception.Response.StatusCode
            try { $detail = $_.ErrorDetails.Message } catch {}
        }
        return @{ ok = $false; status = $status; data = $detail; error = $_.Exception.Message }
    }
}

function Check($label, $condition, $detail) {
    if ($condition) { Write-Host "  PASS  $label" -ForegroundColor DarkGreen }
    else { Write-Host "  FAIL  $label -- $detail" -ForegroundColor Red; $script:Failures++ }
}

function Section($name) { Write-Host "`n$name" -ForegroundColor Cyan }

# ---------------------------------------------------------------------------
# Build real files on disk, including a binary one.
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("netrescue-verify-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp | Out-Null
$textFile = Join-Path $tmp "notes.txt"
$binaryFile = Join-Path $tmp "sample.bin"

$text = "NetRescue multipath transfer lab.`r`n" + ("the quick brown fox jumps over the lazy dog. " * 500)
[System.IO.File]::WriteAllText($textFile, $text, [System.Text.UTF8Encoding]::new($false))

$bytes = New-Object byte[] 20000
for ($i = 0; $i -lt $bytes.Length; $i++) { $bytes[$i] = [byte](($i * 7) % 256) }
[System.IO.File]::WriteAllBytes($binaryFile, $bytes)

Write-Host "NetRescue multipath acceptance -- $Base" -ForegroundColor White
Write-Host "  text:    $textFile ($($text.Length) bytes)" -ForegroundColor DarkGray
Write-Host "  binary:  $binaryFile ($($bytes.Length) bytes)" -ForegroundColor DarkGray

$PATHS = @(
    @{ path_id = "path-1"; fd_estimate_ms = 8;  tp_estimate_bps = 8000000; loss_rate = 0.0 },
    @{ path_id = "path-2"; fd_estimate_ms = 40; tp_estimate_bps = 3000000; loss_rate = 0.0 },
    @{ path_id = "path-3"; fd_estimate_ms = 120; tp_estimate_bps = 1000000; loss_rate = 0.0 }
)

function Upload-File($path, $name, $chunkSize, $paths, $seed) {
    $b64 = [Convert]::ToBase64String([System.IO.File]::ReadAllBytes($path))
    return Call "POST" "/api/xfer/upload" @{
        filename = $name; data_b64 = $b64; chunk_size_bytes = $chunkSize
        paths = $paths; scheduler_variant = "DB-DSP"; retransmit_policy = "RTX-CL"
        max_retries = 5; seed = $seed
    }
}

function Wait-Transfer {
    for ($i = 0; $i -lt 3000; $i++) {
        $s = Call "GET" "/api/xfer/state" $null
        if ($s.ok -and $s.data.transfer.state -ne "RUNNING") { return $s }
        Start-Sleep -Milliseconds 60
    }
    throw "transfer never finished"
}

# ---------------------------------------------------------------------------
Section "Experiment A -- basic text file transfer"
$up = Upload-File $textFile "notes.txt" 4096 $PATHS 99
Check "text file accepted by the backend" $up.ok "$($up.error) $($up.data)"
$t = $up.data.transfer
Check "chunked by the backend" ($t.total_chunks -gt 1) "total_chunks=$($t.total_chunks)"
$srcHash = $t.source_sha256
Check "source SHA-256 computed server-side" ($srcHash.Length -eq 64) $srcHash

Call "POST" "/api/xfer/start" @{} | Out-Null
$done = Wait-Transfer
$t = $done.data.transfer
Check "transfer completed" ($t.state -eq "COMPLETE") "state=$($t.state)"
Check "every chunk delivered" ($t.counts.delivered -eq $t.total_chunks) "$($t.counts.delivered)/$($t.total_chunks)"
Check "SHA-256 matches" ($t.result.integrity_ok -eq $true) "$($t.result.source_sha256) vs $($t.result.receiver_sha256)"

$dl = Join-Path $tmp "downloaded.txt"
try {
    Invoke-WebRequest "$Base/api/xfer/download" -Headers $Headers -OutFile $dl -TimeoutSec 120
    $dlBytes = [System.IO.File]::ReadAllBytes($dl)
    $origBytes = [System.IO.File]::ReadAllBytes($textFile)
    $same = ($dlBytes.Length -eq $origBytes.Length) -and
            ([System.Convert]::ToBase64String($dlBytes) -eq [System.Convert]::ToBase64String($origBytes))
    Check "downloaded file is byte-for-byte the original" $same "length $($dlBytes.Length) vs $($origBytes.Length)"
    $dlHash = (Get-FileHash $dl -Algorithm SHA256).Hash.ToLower()
    Check "downloaded file's own SHA-256 matches" ($dlHash -eq $srcHash) "$dlHash vs $srcHash"
} catch {
    Check "download succeeded" $false $_.Exception.Message
}

# ---------------------------------------------------------------------------
Section "Experiment B -- binary file transfer"
$upB = Upload-File $binaryFile "sample.bin" 4096 $PATHS 5
Check "binary file accepted" $upB.ok "$($upB.error)"
Call "POST" "/api/xfer/start" @{} | Out-Null
$doneB = Wait-Transfer
$tB = $doneB.data.transfer
Check "binary transfer verified" ($tB.result.integrity_ok -eq $true) "state=$($tB.state)"
$dlB = Join-Path $tmp "downloaded.bin"
Invoke-WebRequest "$Base/api/xfer/download" -Headers $Headers -OutFile $dlB -TimeoutSec 120
$dlBBytes = [System.IO.File]::ReadAllBytes($dlB)
$origBBytes = [System.IO.File]::ReadAllBytes($binaryFile)
$sameB = ($dlBBytes.Length -eq $origBBytes.Length) -and
         ([System.Convert]::ToBase64String($dlBBytes) -eq [System.Convert]::ToBase64String($origBBytes))
Check "binary content intact through every byte value" $sameB "length $($dlBBytes.Length) vs $($origBBytes.Length)"

# ---------------------------------------------------------------------------
Section "Experiment C -- out-of-order chunks"
$upC = Upload-File $binaryFile "sample.bin" 2048 $PATHS 7
Call "POST" "/api/xfer/start" @{} | Out-Null
$doneC = Wait-Transfer
$tC = $doneC.data.transfer
Check "chunks arrived out of order" ($tC.out_of_order_count -gt 0) "count=$($tC.out_of_order_count)"
Check "reconstruction still verified" ($tC.result.integrity_ok -eq $true) "state=$($tC.state)"
Check "verified by offset, not arrival order" `
    ($tC.result.receiver_sha256 -eq $tC.result.source_sha256) "hashes differ"
Write-Host ("        arrival order sample: " + (($tC.arrival_order | Select-Object -First 12) -join ', ')) -ForegroundColor DarkGray

# ---------------------------------------------------------------------------
Section "Experiment D -- FIFO vs Priority"
$cmp = Call "POST" "/api/xfer/schedulers/compare" @{
    workload = @{ critical_rate_hz = 40; critical_size_bits = 1600; bulk_rate_hz = 400
                  bulk_size_bits = 40000; duration_ms = 500; max_queue = 200; seed = 4242 }
    link = @{ rate_bps = 5000000; propagation_ms = 1; max_queue = 200 }
}
Check "both schedulers ran" $cmp.ok "$($cmp.error)"
if ($cmp.ok) {
    $names = ($cmp.data.results | ForEach-Object { $_.scheduler }) -join ','
    Check "fifo and priority present" ($names -match "fifo" -and $names -match "priority") $names
    Check "labelled simulated, not measured" ($cmp.data.simulated -eq $true -and $cmp.data.measured_over_real_sockets -eq $false) "not labelled"
    Check "link was congested" ($cmp.data.congested -eq $true) "offered load below capacity"

    $fifo = $cmp.data.results | Where-Object scheduler -eq "fifo"
    $prio = $cmp.data.results | Where-Object scheduler -eq "priority"
    $fCrit = $fifo.classes.critical.latency_median_ms
    $pCrit = $prio.classes.critical.latency_median_ms
    Check "priority lowered critical latency" ($pCrit -lt $fCrit) "fifo=$fCrit priority=$pCrit"
    Write-Host ("        FIFO critical {0} ms / bulk {1:N2} Mbps" -f $fCrit, ($fifo.classes.bulk.throughput_bps / 1e6)) -ForegroundColor DarkGray
    Write-Host ("        PRIO critical {0} ms / bulk {1:N2} Mbps" -f $pCrit, ($prio.classes.bulk.throughput_bps / 1e6)) -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
Section "Experiment E -- retransmission under loss"
$lossy = @(
    @{ path_id = "path-1"; fd_estimate_ms = 10; tp_estimate_bps = 5000000; loss_rate = 0.08 },
    @{ path_id = "path-2"; fd_estimate_ms = 25; tp_estimate_bps = 2000000; loss_rate = 0.05 },
    @{ path_id = "path-3"; fd_estimate_ms = 60; tp_estimate_bps = 1000000; loss_rate = 0.03 }
)
$upE = Upload-File $binaryFile "sample.bin" 2048 $lossy 31
Call "POST" "/api/xfer/start" @{} | Out-Null
$doneE = Wait-Transfer
$tE = $doneE.data.transfer
Check "loss produced real retransmissions" ($tE.retransmission_count -gt 0) "count=$($tE.retransmission_count)"
Check "retries recovered the transfer" ($tE.state -eq "COMPLETE") "state=$($tE.state)"
Check "integrity verified after retransmission" ($tE.result.integrity_ok -eq $true) "hashes differ"
$lostEvents = ($doneE.data.events | Where-Object type -eq "chunk.lost").Count
Check "retry events journalled" ($lostEvents -gt 0) "chunk.lost events=$lostEvents"

$rc = Call "POST" "/api/xfer/variants/compare" @{
    paths = $lossy; payload_bytes = 60000; chunk_size_bytes = 1024; repetitions = 1
    variants = @("DB-DSP"); retransmit_policies = @("RTX-CL", "RTX-CWND"); seed = 5
}
Check "RTX-CL vs RTX-CWND compared" $rc.ok "$($rc.error)"
if ($rc.ok) {
    $keys = ($rc.data.rows | ForEach-Object { "$($_.variant)|$($_.retransmit_policy)" }) -join ', '
    Check "both retransmit policies ran" ($keys -match "RTX-CL" -and $keys -match "RTX-CWND") $keys
}

# ---------------------------------------------------------------------------
Section "Experiment F -- failure handling"
Call "POST" "/api/xfer/reset" $null | Out-Null
$dlBlocked = Call "GET" "/api/xfer/download" $null
Check "download refused with nothing loaded" (-not $dlBlocked.ok -and $dlBlocked.status -eq 409) "status=$($dlBlocked.status)"
$noFile = Call "POST" "/api/xfer/start" @{} $null
Check "starting without a file is a conflict" (-not $noFile.ok -and $noFile.status -eq 409) "status=$($noFile.status)"

# Bounded retries exhausted -> honest failure, no false verification.
$hopeless = @(
    @{ path_id = "path-1"; fd_estimate_ms = 10; tp_estimate_bps = 2000000; loss_rate = 0.9 },
    @{ path_id = "path-2"; fd_estimate_ms = 20; tp_estimate_bps = 2000000; loss_rate = 0.9 }
)
$b64hopeless = [Convert]::ToBase64String([System.IO.File]::ReadAllBytes($binaryFile))
Call "POST" "/api/xfer/upload" @{
    filename = "doomed.bin"; data_b64 = $b64hopeless; chunk_size_bytes = 4096
    paths = $hopeless; scheduler_variant = "DB-DSP"; retransmit_policy = "RTX-CL"
    max_retries = 0; seed = 3
} | Out-Null
Call "POST" "/api/xfer/start" @{} | Out-Null
$failed = Wait-Transfer
$tF = $failed.data.transfer
if ($tF.state -eq "FAILED") {
    Check "bounded failure reported as FAILED" ($true) ""
    Check "no receiver hash invented for an unreconstructed payload" `
        ($null -eq $tF.result.receiver_sha256) "hash=$($tF.result.receiver_sha256)"
    $dlF = Call "GET" "/api/xfer/download" $null
    Check "download refused for an unverified transfer" (-not $dlF.ok -and $dlF.status -eq 409) "status=$($dlF.status)"
} else {
    Check "run recovered despite heavy loss (also valid)" ($tF.result.integrity_ok -eq $true) "state=$($tF.state)"
}

Call "POST" "/api/xfer/reset" $null | Out-Null
$afterReset = Call "GET" "/api/xfer/state" $null
Check "reset cleared the workspace" ($afterReset.data.state -eq "EMPTY") "state=$($afterReset.data.state)"

# ---------------------------------------------------------------------------
Section "Journal and export"
$upJ = Upload-File $textFile "notes.txt" 4096 $PATHS 99
Call "POST" "/api/xfer/start" @{} | Out-Null
$doneJ = Wait-Transfer
$events = Call "GET" "/api/xfer/events" $null
Check "journal has real events" ($events.data.count -gt 0) "count=$($events.data.count)"
$csv = Call "GET" "/api/xfer/export?fmt=csv" $null
Check "CSV export is the journal" ($csv.ok -and $csv.data.rows -gt 0) "rows=$($csv.data.rows)"
$json = Call "GET" "/api/xfer/export?fmt=json" $null
Check "JSON export carries the result and hashes" `
    ($json.ok -and $json.data.content -match 'source_sha256') "no hash in export"

Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
Call "POST" "/api/xfer/reset" $null | Out-Null

Write-Host ""
if ($script:Failures -eq 0) { Write-Host "All acceptance experiments passed." -ForegroundColor Green }
else { Write-Host "$script:Failures check(s) failed." -ForegroundColor Red; exit 1 }