#!/usr/bin/env pwsh
# measure_windows.ps1 — Windows equivalent of measure_1h.sh
# Measures CPU %, Working Set (RAM), disk write bytes, net TX bytes
# for the pipeline process. Samples every 5 seconds for DURATION seconds.
# Usage: pwsh measure_windows.ps1 [-DurationSeconds 600] [-Iface "Ethernet"]

param(
    [int]$DurationSeconds = 600,
    [string]$Iface = "Ethernet",
    [string]$CsvOut = "evidence/resources_1h.csv"
)

# Find pipeline process
$proc = Get-Process -Name "pipeline" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $proc) {
    Write-Error "No 'pipeline' process found. Start the pipeline first."
    exit 1
}

$pid_ = $proc.Id
Write-Host "Measuring pipeline PID=$pid_ for ${DurationSeconds}s, sampling every 5s" -ForegroundColor Cyan

New-Item -ItemType Directory -Force -Path "evidence" | Out-Null
"timestamp,elapsed_s,cpu_pipeline_pct,rss_pipeline_mb,ws_pipeline_mb,disk_write_mb,net_tx_mb" | Set-Content $CsvOut

$startTime   = Get-Date
$startDisk   = (Get-Process -Id $pid_ -ErrorAction SilentlyContinue).DiskWrites / 1MB
$startNet    = (Get-NetAdapterStatistics -Name $Iface -ErrorAction SilentlyContinue).SentBytes / 1MB
$prevCpuMs   = (Get-Process -Id $pid_ -ErrorAction SilentlyContinue).TotalProcessorTime.TotalMilliseconds
$prevSample  = Get-Date
$peakRss     = 0
$peakWs      = 0
$interval    = 5

while ($true) {
    Start-Sleep -Seconds $interval
    $elapsed = [int]((Get-Date) - $startTime).TotalSeconds
    if ($elapsed -ge $DurationSeconds) { break }

    $p = Get-Process -Id $pid_ -ErrorAction SilentlyContinue
    if (-not $p) { Write-Warning "Pipeline process ended."; break }

    # CPU %: delta CPU time / delta wall time * 100
    $nowCpuMs  = $p.TotalProcessorTime.TotalMilliseconds
    $nowSample = Get-Date
    $deltaCpu  = $nowCpuMs  - $prevCpuMs
    $deltaWall = ($nowSample - $prevSample).TotalMilliseconds
    $cpuPct    = [math]::Round($deltaCpu / $deltaWall * 100, 1)
    $prevCpuMs  = $nowCpuMs
    $prevSample = $nowSample

    # RAM
    $rssMb = [math]::Round($p.WorkingSet64  / 1MB, 1)
    $wsMb  = [math]::Round($p.PeakWorkingSet64 / 1MB, 1)
    if ($rssMb -gt $peakRss) { $peakRss = $rssMb }
    if ($wsMb  -gt $peakWs)  { $peakWs  = $wsMb  }

    # Disk writes (cumulative MB since start)
    $diskMb = [math]::Round(($p.DiskWrites / 1MB) - $startDisk, 2)

    # Net TX (cumulative MB since start)
    $netMb  = [math]::Round(((Get-NetAdapterStatistics -Name $Iface -ErrorAction SilentlyContinue).SentBytes / 1MB) - $startNet, 2)

    $ts  = (Get-Date -Format "yyyy-MM-ddTHH:mm:ss")
    "$ts,$elapsed,$cpuPct,$rssMb,$wsMb,$diskMb,$netMb" | Add-Content $CsvOut

    Write-Host "  t=${elapsed}s  CPU=${cpuPct}%  RSS=${rssMb}MB  Disk=${diskMb}MB  NetTX=${netMb}MB"
}

Write-Host "`n=== Summary ===" -ForegroundColor Green
Write-Host "Peak RSS (WorkingSet):     $peakRss MB"
Write-Host "Peak WS (PeakWorkingSet):  $peakWs MB"
$rows = Import-Csv $CsvOut
$avgCpu = [math]::Round(($rows | Measure-Object -Property cpu_pipeline_pct -Average).Average, 1)
$totalDisk = [math]::Round(($rows | Select-Object -Last 1).disk_write_mb, 1)
$totalNet  = [math]::Round(($rows | Select-Object -Last 1).net_tx_mb, 1)
Write-Host "Avg CPU (pipeline):        $avgCpu %"
Write-Host "Total disk written:        $totalDisk MB"
Write-Host "Total network TX:          $totalNet MB"
Write-Host "`nCSV saved to: $CsvOut"
