#!/bin/sh
# Get RAM%
total_mem=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
free_mem=$(awk '/^MemFree:/ {print $2}' /proc/meminfo)
buffers=$(awk '/^Buffers:/ {print $2}' /proc/meminfo)
cached=$(awk '/^Cached:/ {print $2}' /proc/meminfo)
sreclaimable=$(awk '/^SReclaimable:/ {print $2}' /proc/meminfo)

[ -z "$buffers" ] && buffers=0
[ -z "$cached" ] && cached=0
[ -z "$sreclaimable" ] && sreclaimable=0

used_mem=$((total_mem - free_mem - buffers - cached - sreclaimable))
if [ "$total_mem" -gt 0 ]; then
    mem_pct=$(awk -v used="$used_mem" -v tot="$total_mem" 'BEGIN {printf "%.1f", (used/tot)*100}')
else
    mem_pct="0.0"
fi

# Get CPU% (2 reads of /proc/stat with sleep 0.5)
read_cpu_stats() {
    awk '/^cpu / {print $2+$3+$4+$5+$6+$7+$8+$9+$10, $5+$6}' /proc/stat
}

stats1=$(read_cpu_stats)
total1=$(echo "$stats1" | cut -d' ' -f1)
idle1=$(echo "$stats1" | cut -d' ' -f2)

usleep 500000 2>/dev/null || sleep 1

stats2=$(read_cpu_stats)
total2=$(echo "$stats2" | cut -d' ' -f1)
idle2=$(echo "$stats2" | cut -d' ' -f2)

diff_total=$((total2 - total1))
diff_idle=$((idle2 - idle1))

if [ "$diff_total" -gt 0 ]; then
    cpu_pct=$(awk -v idle="$diff_idle" -v tot="$diff_total" 'BEGIN {printf "%.1f", (1 - idle/tot)*100}')
else
    cpu_pct="0.0"
fi

# Get 1-min load average
load1=$(awk '{print $1}' /proc/loadavg)

# Get root disk space used%
disk_pct=$(df / | tail -n 1 | awk '{print $5}' | tr -d '%')

# Output exact JSON
printf '{"mem_pct": %s, "cpu_pct": %s, "load1": %s, "disk_pct": %s}\n' "$mem_pct" "$cpu_pct" "$load1" "$disk_pct"
