# scripts/verify_playground.ps1
#
# Runs the Feature A acceptance experiments against a *running* API, over HTTP,
# exactly as the browser does — no imports, no in-process shortcuts. If this
# passes, the endpoints the playground UI calls are behaving; what it cannot
# check is the rendering.
#
#   .\scripts\verify_playground.ps1                       # against 127.0.0.1:8000
#   .\scripts\verify_playground.ps1 -Base http://127.0.0.1:8001

param([string]$Base = "http://127.0.0.1:8000")

$ErrorActionPreference = "Stop"
$Headers = @{ "X-NetRescue-Principal" = "verify"; "X-NetRescue-Role" = "operator" }
$script:Failures = 0

function Call($method, $path, $body) {
    $params = @{ Method = $method; Uri = "$Base$path"; Headers = $Headers; TimeoutSec = 60 }
    if ($null -ne $body) {
        $params.Body = ($body | ConvertTo-Json -Depth 12)
        $params.ContentType = "application/json"
    }
    try {
        $r = Invoke-RestMethod @params
        return @{ ok = $true; status = 200; data = $r }
    } catch {
        $status = 0
        $detail = $null
        if ($_.Exception.Response) {
            $status = [int]$_.Exception.Response.StatusCode
            try { $detail = $_.ErrorDetails.Message } catch {}
        }
        return @{ ok = $false; status = $status; data = $detail; error = $_.Exception.Message }
    }
}

function Check($label, $condition, $detail) {
    if ($condition) {
        Write-Host "  PASS  $label" -ForegroundColor DarkGreen
    } else {
        Write-Host "  FAIL  $label -- $detail" -ForegroundColor Red
        $script:Failures++
    }
}

function Section($name) { Write-Host "`n$name" -ForegroundColor Cyan }

$EXAMPLE = @{
    nodes = @(
        @{ node_id = "A"; label = "Source A";       role = "source";      x = 90;  y = 200 }
        @{ node_id = "B"; label = "Router B";       role = "router";      x = 320; y = 90 }
        @{ node_id = "C"; label = "Router C";       role = "router";      x = 320; y = 310 }
        @{ node_id = "D"; label = "Router D";       role = "router";      x = 610; y = 200 }
        @{ node_id = "E"; label = "Destination E";  role = "destination"; x = 880; y = 200 }
    )
    links = @(
        @{ node_a = "A"; node_b = "B" }, @{ node_a = "A"; node_b = "C" },
        @{ node_a = "B"; node_b = "D" }, @{ node_a = "C"; node_b = "D" },
        @{ node_a = "D"; node_b = "E" }
    )
    source = "A"; destination = "E"
}

$CHAIN = @{
    nodes = @(
        @{ node_id = "A"; role = "source" }, @{ node_id = "B"; role = "router" },
        @{ node_id = "C"; role = "destination" }
    )
    links = @(@{ node_a = "A"; node_b = "B" }, @{ node_a = "B"; node_b = "C" })
    source = "A"; destination = "C"
}

Write-Host "NetRescue playground acceptance -- $Base" -ForegroundColor White

# ---------------------------------------------------------------------------
Section "Experiment A -- basic connectivity"
Call "POST" "/api/playground/topology/clear" $null | Out-Null
$saved = Call "PUT" "/api/playground/topology" $EXAMPLE
Check "topology saved" $saved.ok "status $($saved.status)"
$applied = Call "POST" "/api/playground/apply" @{ heartbeat_interval_s = 0.5 }
Check "network applied" ($applied.ok -and $applied.data.running) "$($applied.error)$($applied.data)"
Check "five real listeners" (($applied.data.nodes | Where-Object { $_.port }).Count -eq 5) "not every node holds a port"
Check "canvas matches the network" ($applied.data.draft_is_running -eq $true) "draft drifted from running"

$first = Call "POST" "/api/playground/test" @{ source = "A"; destination = "E" }
Check "packet delivered" ($first.ok -and $first.data.delivered -eq $true) "$($first.data)"
Check "route is A-B-D-E" (($first.data.path -join "-") -eq "A-B-D-E") "got $($first.data.path -join '-')"

# ---------------------------------------------------------------------------
Section "Experiment B -- fail a link"
$cut = Call "POST" "/api/playground/links/A-B/fail" $null
Check "link A-B is DOWN" ($cut.ok -and ($cut.data.links | Where-Object link_id -eq "A-B").status -eq "DOWN") "$($cut.error)"
$second = Call "POST" "/api/playground/test" @{ source = "A"; destination = "E" }
Check "still delivered" ($second.data.delivered -eq $true) "$($second.data)"
Check "rerouted via C" (($second.data.path -join "-") -eq "A-C-D-E") "got $($second.data.path -join '-')"
Check "failed link unused" (($second.data.path -notcontains "B")) "B is on the path"

# ---------------------------------------------------------------------------
Section "Experiment C -- restore, then fail a node"
$restored = Call "POST" "/api/playground/links/A-B/restore" $null
$third = Call "POST" "/api/playground/test" @{ source = "A"; destination = "E" }
Check "short path preferred again" (($third.data.path -join "-") -eq "A-B-D-E") "got $($third.data.path -join '-')"

$killed = Call "POST" "/api/playground/nodes/B/fail" $null
Check "node B FAILED" ($killed.ok -and ($killed.data.nodes | Where-Object node_id -eq "B").failed -eq $true) "$($killed.error)"
$fourth = Call "POST" "/api/playground/test" @{ source = "A"; destination = "E" }
Check "delivered around B" ($fourth.data.delivered -eq $true) "$($fourth.data)"
Check "path avoids B" (($fourth.data.path -notcontains "B")) "got $($fourth.data.path -join '-')"

# ---------------------------------------------------------------------------
Section "Experiment E -- restore the node"
$reborn = Call "POST" "/api/playground/nodes/B/restore" $null
Check "node B listening again" ($reborn.ok -and ($reborn.data.nodes | Where-Object node_id -eq "B").running -eq $true) "$($reborn.error)"
$fifth = Call "POST" "/api/playground/test" @{ source = "A"; destination = "E" }
Check "delivered" ($fifth.data.delivered -eq $true) "$($fifth.data)"
Check "via B again" (($fifth.data.path -join "-") -eq "A-B-D-E") "got $($fifth.data.path -join '-')"

# ---------------------------------------------------------------------------
Section "Experiment D -- no alternate route"
Call "PUT" "/api/playground/topology" $CHAIN | Out-Null
$chain = Call "POST" "/api/playground/apply" @{ heartbeat_interval_s = 0.5 }
Check "chain applied" ($chain.ok -and $chain.data.running) "$($chain.error)"
$ok = Call "POST" "/api/playground/test" @{ source = "A"; destination = "C" }
Check "chain delivers" ($ok.data.delivered -eq $true) "$($ok.data)"
Call "POST" "/api/playground/nodes/B/fail" $null | Out-Null
$dead = Call "POST" "/api/playground/test" @{ source = "A"; destination = "C" }
Check "delivery fails" ($dead.data.delivered -eq $false) "$($dead.data)"
Check "with a reason" ([bool]$dead.data.reason) "no reason given"
$state = Call "GET" "/api/playground/state" $null
Check "route reported UNREACHABLE" ($null -eq $state.data.route) "route is $($state.data.route)"

# ---------------------------------------------------------------------------
Section "Experiment F -- hysteresis"
Call "PUT" "/api/playground/topology" $EXAMPLE | Out-Null
Call "POST" "/api/playground/apply" @{ heartbeat_interval_s = 0.2; f_thresh = 1; n = 2; r_thresh = 3 } | Out-Null
$det = Call "POST" "/api/playground/detectors" @{ link_id = "A-B"; f_thresh = 1; n = 2; r_thresh = 3 }
Check "detector retuned on both ends" ($det.ok -and $det.data.detectors.Count -eq 2) "$($det.error)"

Write-Host "  stopping B and watching the detector cross its thresholds..." -ForegroundColor DarkGray
Call "POST" "/api/playground/nodes/B/fail" $null | Out-Null
$seen = @()
$deadline = (Get-Date).AddSeconds(20)
while ((Get-Date) -lt $deadline) {
    $t = Call "GET" "/api/playground/transitions?link_id=A-B" $null
    if ($t.ok -and $t.data.transitions.Count -gt 0) {
        $last = $t.data.transitions[-1]
        if ($seen.Count -eq 0 -or $seen[-1] -ne $last.to_state) { $seen += $last.to_state }
        if ($last.to_state -eq "FAILED") { break }
    }
    Start-Sleep -Milliseconds 200
}
Check "detector reached FAILED on real missed heartbeats" ($seen -contains "FAILED") "saw $($seen -join ' -> ')"
Check "passed through SUSPECT" ($seen -contains "SUSPECT") "saw $($seen -join ' -> ')"

Call "POST" "/api/playground/nodes/B/restore" $null | Out-Null
$recovered = $false
$deadline = (Get-Date).AddSeconds(20)
while ((Get-Date) -lt $deadline) {
    $d = Call "GET" "/api/playground/detectors" $null
    # Deserialised JSON is a PSCustomObject, not a hashtable, so the nested
    # lookup goes through PSObject.Properties rather than an index.
    if ($d.ok) {
        $atA = $d.data.detectors.PSObject.Properties | Where-Object { $_.Name -eq "A" }
        $reading = $atA.Value.PSObject.Properties | Where-Object { $_.Name -eq "B" }
        if ($reading -and $reading.Value.state -eq "HEALTHY") { $recovered = $true; break }
    }
    Start-Sleep -Milliseconds 250
}
Check "detector recovered to HEALTHY" $recovered "never returned to HEALTHY"

$sequence = @($true,$true,$true,$false,$false,$false,$false,$true,$true,$true,$true,$false,$false,$false,$false,$true,$true,$true,$true,$true)
$cmp = Call "POST" "/api/playground/hysteresis/compare" @{
    link_id = "A-B"; sequence = $sequence; f_thresh = 3; n = 5; r_thresh = 6; baseline_threshold = 3
}
Check "comparison ran on the recorded sequence" ($cmp.ok -and $cmp.data.timeline.Count -eq $sequence.Count) "$($cmp.error)"
if ($cmp.ok) {
    Check "naive flapped more than hysteresis" `
        ($cmp.data.naive.route_flap_count -gt $cmp.data.hysteresis.route_flap_count) `
        "hyst=$($cmp.data.hysteresis.route_flap_count) naive=$($cmp.data.naive.route_flap_count)"
    Write-Host ("        hysteresis {0} route changes, single threshold {1}" -f `
        $cmp.data.hysteresis.route_flap_count, $cmp.data.naive.route_flap_count) -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
Section "Journal and export"
$events = Call "GET" "/api/playground/events" $null
Check "journal has entries" ($events.data.count -gt 0) "empty"
$csv = Call "GET" "/api/playground/export?fmt=csv" $null
Check "CSV export is real" ($csv.ok -and $csv.data.rows -gt 0 -and $csv.data.content -match "seq,timestamp") "no rows"
$json = Call "GET" "/api/playground/export?fmt=json" $null
Check "JSON export is the whole experiment" ($json.ok -and $json.data.content -match '"packets"') "missing packets"

Call "POST" "/api/playground/teardown" $null | Out-Null

Write-Host ""
if ($script:Failures -eq 0) {
    Write-Host "All acceptance experiments passed." -ForegroundColor Green
} else {
    Write-Host "$script:Failures check(s) failed." -ForegroundColor Red
    exit 1
}