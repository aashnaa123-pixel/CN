<#
    scripts/free_port.ps1 — release a TCP port held by a stale process.

    Called automatically before the API launches. A leftover uvicorn from an
    earlier session keeps 127.0.0.1:8000 bound and the new one dies with
    "address already in use", which looks like the app is broken when it is
    not.
#>
param([int]$Port = 8000)

$conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue

if (-not $conns) {
    Write-Host "Port $Port is free." -ForegroundColor DarkGray
    exit 0
}

foreach ($c in $conns) {
    $proc = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
    $name = if ($proc) { $proc.ProcessName } else { "unknown" }
    Write-Host "Freeing port $Port (pid $($c.OwningProcess), $name)" -ForegroundColor Yellow
    Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 2
Write-Host "Port $Port released." -ForegroundColor Green