#!/usr/bin/env bash
# measure_1h.sh — Measure CPU, RSS, disk write, network TX over 1 hour.
# Samples every 5 seconds; writes CSV to evidence/resources_1h.csv.
# Run: bash scripts/resources/measure_1h.sh <pipeline_pid>

set -euo pipefail
PIPELINE_PID="${1:-$(pgrep -f 'pipeline run' | head -1)}"
IFACE="${2:-eth0}"   # network interface
DURATION=3600        # 1 hour in seconds
INTERVAL=5
CSV="evidence/resources_1h.csv"
mkdir -p evidence

if [ -z "$PIPELINE_PID" ]; then
  echo "ERROR: pipeline PID not found. Pass PID as arg or start 'pipeline run' first."
  exit 1
fi

echo "Measuring PID=$PIPELINE_PID iface=$IFACE for ${DURATION}s" >&2

# CSV header
echo "timestamp,elapsed_s,cpu_total_pct,cpu_pipeline_pct,rss_pipeline_kb,vmhwm_pipeline_kb,disk_write_kb,net_tx_kb" > "$CSV"

# Baseline /proc/stat values
read_cpu_total() { awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8}' /proc/stat; }
read_cpu_idle()  { awk '/^cpu /{print $5}' /proc/stat; }
read_proc_stat() { awk '{print $14+$15}' /proc/"$1"/stat 2>/dev/null || echo 0; }
read_net_tx()    { awk -v iface="$IFACE:" '$1==iface{print $10}' /proc/net/dev 2>/dev/null || echo 0; }
read_disk_write(){ awk '/write_bytes/{print $2}' /proc/"$1"/io 2>/dev/null || echo 0; }
read_vmhwm()     { awk '/VmHWM/{print $2}' /proc/"$1"/status 2>/dev/null || echo 0; }

PREV_CPU_TOTAL=$(read_cpu_total)
PREV_CPU_IDLE=$(read_cpu_idle)
PREV_PROC_STAT=$(read_proc_stat "$PIPELINE_PID")
PREV_NET_TX=$(read_net_tx)
PREV_DISK_WRITE=$(read_disk_write "$PIPELINE_PID")
START_TIME=$(date +%s)

echo "Sampling every ${INTERVAL}s..." >&2

ELAPSED=0
while [ $ELAPSED -lt $DURATION ]; do
  sleep $INTERVAL
  ELAPSED=$(( $(date +%s) - START_TIME ))
  NOW=$(date -Iseconds)

  # CPU total %
  CUR_TOTAL=$(read_cpu_total)
  CUR_IDLE=$(read_cpu_idle)
  DELTA_TOTAL=$(( CUR_TOTAL - PREV_CPU_TOTAL ))
  DELTA_IDLE=$(( CUR_IDLE  - PREV_CPU_IDLE  ))
  if [ $DELTA_TOTAL -gt 0 ]; then
    CPU_TOTAL_PCT=$(echo "scale=1; 100 * ($DELTA_TOTAL - $DELTA_IDLE) / $DELTA_TOTAL" | bc)
  else
    CPU_TOTAL_PCT=0
  fi

  # Pipeline CPU %: jiffies delta / (INTERVAL * HZ)
  HZ=$(getconf CLK_TCK)
  CUR_PROC=$(read_proc_stat "$PIPELINE_PID")
  DELTA_PROC=$(( CUR_PROC - PREV_PROC_STAT ))
  CPU_PIPELINE_PCT=$(echo "scale=1; 100 * $DELTA_PROC / ($INTERVAL * $HZ)" | bc)

  # RSS
  RSS_KB=$(awk '/VmRSS/{print $2}' /proc/"$PIPELINE_PID"/status 2>/dev/null || echo 0)
  VMHWM_KB=$(read_vmhwm "$PIPELINE_PID")

  # Disk write delta
  CUR_DISK=$(read_disk_write "$PIPELINE_PID")
  DISK_DELTA_KB=$(( (CUR_DISK - PREV_DISK_WRITE) / 1024 ))

  # Net TX delta
  CUR_TX=$(read_net_tx)
  TX_DELTA_KB=$(( (CUR_TX - PREV_NET_TX) / 1024 ))

  echo "$NOW,$ELAPSED,$CPU_TOTAL_PCT,$CPU_PIPELINE_PCT,$RSS_KB,$VMHWM_KB,$DISK_DELTA_KB,$TX_DELTA_KB" >> "$CSV"

  PREV_CPU_TOTAL=$CUR_TOTAL
  PREV_CPU_IDLE=$CUR_IDLE
  PREV_PROC_STAT=$CUR_PROC
  PREV_DISK_WRITE=$CUR_DISK
  PREV_NET_TX=$CUR_TX

  echo -n "." >&2
done

echo "" >&2
echo "Done. CSV saved to $CSV" >&2

# Summary
echo "=== Summary ===" >&2
python3 -c "
import csv, sys
rows=list(csv.DictReader(open('$CSV')))
if not rows: sys.exit(0)
def avg(col): return sum(float(r[col]) for r in rows)/len(rows)
def mx(col):  return max(float(r[col]) for r in rows)
print(f'Avg CPU total:      {avg(\"cpu_total_pct\"):.1f}%')
print(f'Avg CPU pipeline:   {avg(\"cpu_pipeline_pct\"):.1f}%')
print(f'Peak RSS:           {mx(\"rss_pipeline_kb\")/1024:.0f} MB')
print(f'Peak VmHWM:         {mx(\"vmhwm_pipeline_kb\")/1024:.0f} MB')
print(f'Total disk written: {sum(float(r[\"disk_write_kb\"]) for r in rows)/1024:.1f} MB')
print(f'Total net TX:       {sum(float(r[\"net_tx_kb\"]) for r in rows)/1024:.1f} MB')
" >&2
