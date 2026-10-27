<#
    scripts/verify_feature2.ps1 — verify Feature 2, "Multipath Transfer".

    Feature card 02 on the site. Three checks against the running API:

      1. the five scheduler variants are registered
      2. the full comparison matrix, 5 variants x 2 RTX policies x 5 reps
      3. one real transfer with SHA-256 source == receiver

    Requires the API on 127.0.0.1:8000 (press F5 in VS Code to start it).
#>
param([string]$Base = "http://127.0.0.1:8000")

$ErrorActionPreference = "Stop"
$headers = @{
    "X-NetRescue-Principal" = "operator1"
    "X-NetRescue-Role"      = "operator"
}

Write-Host ""
Write-Host "  FEATURE 2 - Multipath Transfer" -ForegroundColor Cyan
Write-Host "  ==================================" -ForegroundColor Cyan

# --- 2a. registered variants -------------------------------------------------
Write-Host ""
Write-Host "  [2a] Registered scheduler variants" -ForegroundColor Yellow
try {
    $v = Invoke-RestMethod "$Base/api/transfers/variants" -Method Get -Headers $headers
    $ids = $v.scheduler_variants | ForEach-Object { $_.id }
    Write-Host "  $($ids -join ', ')"
    Write-Host "  Policies: $(($v.retransmit_policies | ForEach-Object { $_.id }) -join ', ')"
    $expected = @("DB-DSP", "RR", "WRR", "SINGLE", "NAIVE_DELAY")
    if ((Compare-Object $expected $ids).Count -eq 0) {
        Write-Host "  PASS - all 5 variants registered" -ForegroundColor Green
    } else {
        Write-Host "  FAIL - expected $($expected -join ', ')" -ForegroundColor Red
    }
} catch {
    Write-Host "  FAILED: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

# --- 2b. full comparison matrix ---------------------------------------------
Write-Host ""
Write-Host "  [2b] Scheduler comparison matrix" -ForegroundColor Yellow
Write-Host "  Running 5 variants x 2 policies x 5 reps = 50 transfers. Please wait."
try {
    $body = @{
        paths = @(
            @{ path_id = "p0"; fd_ms = 10.0; tp_bps = 1000000  },
            @{ path_id = "p1"; fd_ms = 12.0; tp_bps = 50000000 },
            @{ path_id = "p2"; fd_ms = 14.0; tp_bps = 50000000 }
        )
        payload_bytes    = 120000
        chunk_size_bytes = 32768
        repetitions      = 5
    } | ConvertTo-Json -Depth 5

    $cmp = Invoke-RestMethod "$Base/api/benchmarks/scheduler-comparison" `
        -Method Post -Headers $headers -Body $body `
        -ContentType "application/json" -TimeoutSec 900

    Write-Host ""
    Write-Host $cmp.table

    # The API serialises each row with flattened keys (duration_s_median, not
    # duration_s.median) and integrity_ok as an "n/n" string.
    $dsp    = $cmp.rows | Where-Object { $_.variant -eq "DB-DSP"      -and $_.retransmit_policy -eq "RTX-CL" }
    $wrr    = $cmp.rows | Where-Object { $_.variant -eq "WRR"         -and $_.retransmit_policy -eq "RTX-CL" }
    $single = $cmp.rows | Where-Object { $_.variant -eq "SINGLE"      -and $_.retransmit_policy -eq "RTX-CL" }
    $naive  = $cmp.rows | Where-Object { $_.variant -eq "NAIVE_DELAY" -and $_.retransmit_policy -eq "RTX-CL" }

    Write-Host ""
    Write-Host "  Median duration / goodput per variant (RTX-CL):"
    foreach ($row in @($dsp, $wrr, $single, $naive)) {
        Write-Host ("    {0,-12} {1,8} s   {2,14} bps   integrity {3}" -f `
            $row.variant, $row.duration_s_median, [math]::Round($row.goodput_bps_median), $row.integrity_ok)
    }

    # Every cell must report a clean 5/5.
    $bad = $cmp.rows | Where-Object { $_.integrity_ok -ne "$($_.repetitions)/$($_.repetitions)" }
    if (-not $bad) {
        Write-Host "  PASS - SHA-256 matched on all $($cmp.rows.Count) cells" -ForegroundColor Green
    } else {
        Write-Host "  FAIL - $($bad.Count) cell(s) failed integrity: $($bad.variant -join ', ')" -ForegroundColor Red
    }

    if ($dsp.duration_s_median -lt $naive.duration_s_median) {
        $speedup = [math]::Round($naive.duration_s_median / $dsp.duration_s_median, 1)
        Write-Host "  PASS - DB-DSP beat the Naive Delay-Aware ablation (${speedup}x faster)" -ForegroundColor Green
    } else {
        Write-Host "  FAIL - DB-DSP did not beat the ablation" -ForegroundColor Red
    }

    if ($dsp.duration_s_median -lt $single.duration_s_median) {
        $speedup = [math]::Round($single.duration_s_median / $dsp.duration_s_median, 1)
        Write-Host "  PASS - multipath beat single-path TCP (${speedup}x faster)" -ForegroundColor Green
    } else {
        Write-Host "  FAIL - multipath did not beat single-path TCP" -ForegroundColor Red
    }

    Write-Host ""
    Write-Host "  Findings:"
    $cmp.findings | ForEach-Object { Write-Host "    - $_" -ForegroundColor DarkGray }
} catch {
    Write-Host "  FAILED: $($_.Exception.Message)" -ForegroundColor Red
}

# --- 2c. one real transfer ---------------------------------------------------
Write-Host ""
Write-Host "  [2c] Single transfer with integrity check" -ForegroundColor Yellow
try {
    $data = [Convert]::ToBase64String(
        [Text.Encoding]::UTF8.GetBytes(("NetRescue multipath payload " * 400))
    )
    $tBody = @{
        run_id           = "vscode-demo"
        data_b64         = $data
        scheduler_variant = "DB-DSP"
        chunk_size_bytes = 4096
        paths = @(
            @{ path_id = "p0"; fd_estimate_ms = 10.0; tp_estimate_bps = 1000000.0;  loss_rate = 0.0  },
            @{ path_id = "p1"; fd_estimate_ms = 12.0; tp_estimate_bps = 50000000.0; loss_rate = 0.01 }
        )
    } | ConvertTo-Json -Depth 5

    $t = Invoke-RestMethod "$Base/api/transfers" -Method Post -Headers $headers `
        -Body $tBody -ContentType "application/json" -TimeoutSec 300

    Write-Host "  transfer_id       : $($t.transfer_id)"
    Write-Host "  status            : $($t.status)"
    Write-Host "  variant           : $($t.scheduler_variant)"
    Write-Host "  chunks            : $($t.delivered_chunks)/$($t.total_chunks) delivered, $($t.failed_chunks) failed"
    Write-Host "  SHA-256 verified  : $($t.integrity_ok)"
    Write-Host "  duration          : $($t.duration_s) s"
    Write-Host "  retransmissions   : $($t.retransmission_count)"
    Write-Host "  persisted         : $($t.persisted)"
    Write-Host "  per-path chunks   : $(($t.per_path | ForEach-Object { "$($_.path_id)=$($_.chunks_sent)" }) -join ', ')"

    if ($t.status -eq "COMPLETE" -and $t.integrity_ok -and $t.failed_chunks -eq 0) {
        Write-Host "  PASS - transfer completed with matching hashes" -ForegroundColor Green
    } else {
        Write-Host "  FAIL - transfer did not complete cleanly" -ForegroundColor Red
    }
} catch {
    Write-Host "  FAILED: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host ""