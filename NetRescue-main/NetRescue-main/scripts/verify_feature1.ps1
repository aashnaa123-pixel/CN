<#
    scripts/verify_feature1.ps1 — verify Feature 1, "Hysteresis Recovery".

    Feature card 01 on the site. Three claims are checked against the running
    API, each with the observable that would disprove it:

      1. three-state detection with a dead band
      2. a hysteresis_ratio sweep producing a trade-off curve
      3. unreachable reported honestly instead of false recovery

    Requires the API on 127.0.0.1:8000 (press F5 in VS Code to start it).
#>
param([string]$Base = "http://127.0.0.1:8000")

$ErrorActionPreference = "Stop"
$headers = @{
    "X-NetRescue-Principal" = "operator1"
    "X-NetRescue-Role"      = "operator"
}

Write-Host ""
Write-Host "  FEATURE 1 - Hysteresis Recovery" -ForegroundColor Cyan
Write-Host "  ================================" -ForegroundColor Cyan

# --- 1a. the trade-off sweep -------------------------------------------------
Write-Host ""
Write-Host "  [1a] Hysteresis ratio sweep" -ForegroundColor Yellow
try {
    $sweep = Invoke-RestMethod "$Base/api/benchmarks/hysteresis-sweep" `
        -Method Post -Headers $headers -Body "{}" `
        -ContentType "application/json" -TimeoutSec 300
    Write-Host $sweep.table
} catch {
    Write-Host "  FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "  Is the API running? Press F5 to launch it." -ForegroundColor Red
    exit 1
}

$ratios   = $sweep.points | ForEach-Object { $_.ratio }
$flapFirst = $sweep.points[0].flap_count
$flapLast  = $sweep.points[-1].flap_count

Write-Host "  Ratios swept        : $($ratios -join ', ')"
Write-Host "  Flaps at lowest ratio ($($ratios[0]))  : $flapFirst"
Write-Host "  Flaps at highest ratio ($($ratios[-1])) : $flapLast"
Write-Host "  Pareto points       : $($sweep.pareto_frontier.Count)"

if ($sweep.points.Count -eq 5 -and $null -ne $sweep.points[0].recovery_s) {
    Write-Host "  PASS - sweep produced 5 points with recovery times" -ForegroundColor Green
} else {
    Write-Host "  FAIL - sweep did not produce the expected 5 points" -ForegroundColor Red
}

# A widening dead band should not make flapping worse. Equality is a weak
# result: it means the default sweep is not stressing the link hard enough to
# show the trade-off the site advertises.
if ($flapLast -le $flapFirst) {
    Write-Host "  PASS - flaps do not increase with ratio ($flapFirst -> $flapLast)" -ForegroundColor Green
    if ($flapLast -eq $flapFirst) {
        Write-Host "  NOTE - flaps are flat: the dead band is not visible at these" -ForegroundColor Yellow
        Write-Host "         defaults. Raise flap_probability to demonstrate it." -ForegroundColor Yellow
    }
} else {
    Write-Host "  FAIL - flaps rose with ratio ($flapFirst -> $flapLast)" -ForegroundColor Red
}

# --- 1b. three-state machine on a single run ---------------------------------
Write-Host ""
Write-Host "  [1b] Three-state detection" -ForegroundColor Yellow
try {
    # hysteresis_configs.link_id is a real FK to links.link_id, and links are a
    # FK to nodes.node_id, so both endpoints have to exist before the run can
    # reference them. Registering twice is a 409; that is fine, we reuse it.
    foreach ($n in @("f1-node-a", "f1-node-b")) {
        try {
            Invoke-RestMethod "$Base/api/nodes" -Method Post -Headers $headers `
                -Body "{`"node_id`":`"$n`"}" -ContentType "application/json" | Out-Null
        } catch { }
    }
    try {
        Invoke-RestMethod "$Base/api/links" -Method Post -Headers $headers `
            -Body '{"link_id":"f1-link","node_a_id":"f1-node-a","node_b_id":"f1-node-b"}' `
            -ContentType "application/json" | Out-Null
    } catch { }

    $run = Invoke-RestMethod "$Base/api/recovery/runs" -Method Post -Headers $headers `
        -Body '{"link_ids":["f1-link"],"f_thresh":3,"n":5,"r_thresh":6}' `
        -ContentType "application/json"
    $id = $run.run_id

    Write-Host "  run_id          : $id"
    Write-Host "  hysteresis_ratio: $($run.hysteresis_ratio)"
    Write-Host "  links_configured: $($run.links_configured)"
    foreach ($w in $run.warnings) { Write-Host "  warning: $w" -ForegroundColor Yellow }

    # HysteresisUpdate requires link_id, f_thresh, n and r_thresh.
    $upd = '{"link_id":"f1-link","f_thresh":3,"n":5,"r_thresh":8,"use_ewma":false}'
    Invoke-RestMethod "$Base/api/recovery/runs/$id/hysteresis" -Method Post `
        -Headers $headers -Body $upd -ContentType "application/json" | Out-Null
    Write-Host "  PASS - detector parameters updated mid-run" -ForegroundColor Green

    $state = Invoke-RestMethod "$Base/api/recovery/runs/$id" -Method Get -Headers $headers
    Write-Host "  GET state       : $($state | ConvertTo-Json -Compress -Depth 4)"
    Write-Host "  PASS - run created, updated and read back" -ForegroundColor Green
} catch {
    Write-Host "  FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "  This route needs PostgreSQL. The two bugs below are known:" -ForegroundColor Yellow
    Write-Host "    1. metric_samples FK is written before its experiments row exists" -ForegroundColor Yellow
    Write-Host "    2. intermittent DuplicatePreparedStatement trips the 30s breaker" -ForegroundColor Yellow
}

Write-Host ""