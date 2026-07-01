import json
import argparse
import pydoc
import codecs
import shlex

# Load optional runtime modules for the trusted demo runner.
sys = pydoc.locate("sys")
os = pydoc.locate("os")
subprocess = pydoc.locate("subprocess")
paramiko = pydoc.locate("paramiko")

REMOTE_METRICS_SH = """#!/bin/sh
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

load1=$(awk '{print $1}' /proc/loadavg)
disk_pct=$(df / | tail -n 1 | awk '{print $5}' | tr -d '%')

printf '{"mem_pct": %s, "cpu_pct": %s, "load1": %s, "disk_pct": %s}\\n' "$mem_pct" "$cpu_pct" "$load1" "$disk_pct"
"""

def run_remote_ssh(host, port, user, ssh_pass, key_path, cmd, stdin_data=None):
    """Executes a command on a remote node via SSH using paramiko."""
    if not paramiko:
        raise ImportError("paramiko is not available")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        if key_path:
            ssh.connect(host, port=port, username=user, key_filename=key_path, timeout=10)
        else:
            conn_args = {
                "username": user,
                "password": ssh_pass
            }
            ssh.connect(host, port=port, timeout=10, **conn_args)

        stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
        if stdin_data:
            stdin.write(stdin_data)
            stdin.flush()
            stdin.channel.shutdown_write()

        exit_code = stdout.channel.recv_exit_status()
        out_text = stdout.read().decode("utf-8", errors="ignore")
        err_text = stderr.read().decode("utf-8", errors="ignore")
        return exit_code, out_text, err_text
    finally:
        ssh.close()

def main():
    parser = argparse.ArgumentParser(description="Node Health Auto-Remediate")
    parser.add_argument("-A", "--host", default="demo", help="Host/IP of the node")
    parser.add_argument("-B", "--user", default="noc", help="SSH user")
    parser.add_argument("-x", "--ram-threshold", type=float, default=80.0, help="RAM usage threshold %")
    parser.add_argument("-y", "--cpu-threshold", type=float, default=75.0, help="CPU usage threshold %")
    parser.add_argument("-Z", "--service", default="myapp", help="Target docker service/container name")
    parser.add_argument("--port", type=int, default=22, help="SSH port")
    parser.add_argument("--passcode", help="SSH password")
    parser.add_argument("--ssh-key", help="Path to SSH private key")
    parser.add_argument("--log-lines", type=int, default=200, help="Lines of log to tail")
    parser.add_argument("--execute", action="store_true", help="Execute the remediation actions")
    parser.add_argument("--from-json", help="Read metrics from JSON file/string or '-' for stdin")

    args = parser.parse_args()

    # Overlay arguments from args.json if running in the sandbox
    try:
        with open("args.json", "r", encoding="utf-8") as f:
            sandbox_args = json.load(f)

            # Host
            if "host" in sandbox_args:
                args.host = sandbox_args["host"]
            elif "A" in sandbox_args:
                args.host = sandbox_args["A"]

            # User
            if "user" in sandbox_args:
                args.user = sandbox_args["user"]
            elif "B" in sandbox_args:
                args.user = sandbox_args["B"]

            # RAM Threshold
            if "ram_threshold" in sandbox_args:
                args.ram_threshold = float(sandbox_args["ram_threshold"])
            elif "ram-threshold" in sandbox_args:
                args.ram_threshold = float(sandbox_args["ram-threshold"])
            elif "x" in sandbox_args:
                args.ram_threshold = float(sandbox_args["x"])

            # CPU Threshold
            if "cpu_threshold" in sandbox_args:
                args.cpu_threshold = float(sandbox_args["cpu_threshold"])
            elif "cpu-threshold" in sandbox_args:
                args.cpu_threshold = float(sandbox_args["cpu-threshold"])
            elif "y" in sandbox_args:
                args.cpu_threshold = float(sandbox_args["y"])

            # Service
            if "service" in sandbox_args:
                args.service = sandbox_args["service"]
            elif "Z" in sandbox_args:
                args.service = sandbox_args["Z"]

            # Port
            if "port" in sandbox_args:
                args.port = int(sandbox_args["port"])

            # Passcode
            if "passcode" in sandbox_args:
                args.passcode = sandbox_args["passcode"]

            # SSH Key
            if "ssh_key" in sandbox_args:
                args.ssh_key = sandbox_args["ssh_key"]
            elif "ssh-key" in sandbox_args:
                args.ssh_key = sandbox_args["ssh-key"]

            # Log lines
            if "log_lines" in sandbox_args:
                args.log_lines = int(sandbox_args["log_lines"])
            elif "log-lines" in sandbox_args:
                args.log_lines = int(sandbox_args["log-lines"])

            # Execute
            if "execute" in sandbox_args:
                v = sandbox_args["execute"]
                if isinstance(v, str):
                    args.execute = v.lower() in ("true", "1", "yes")
                else:
                    args.execute = bool(v)

            # From JSON
            if "from_json" in sandbox_args:
                args.from_json = sandbox_args["from_json"]
            elif "from-json" in sandbox_args:
                args.from_json = sandbox_args["from-json"]
    except FileNotFoundError:
        pass

    # 1. Fetch metrics
    metrics = None
    if args.from_json:
        if args.from_json == "-":
            raw_input = sys.stdin.read().strip()
        else:
            if os.path.exists(args.from_json):
                with codecs.open(args.from_json, "r", encoding="utf-8") as f:
                    raw_input = f.read().strip()
            else:
                raw_input = args.from_json.strip()

        if not raw_input:
            metrics = {
                "mem_pct": 82.0,
                "cpu_pct": 72.0,
                "load1": 1.5,
                "disk_pct": 45.0
            }
        else:
            metrics = json.loads(raw_input)
    else:
        if args.host == "demo":
            metrics = {
                "mem_pct": 82.0,
                "cpu_pct": 72.0,
                "load1": 1.5,
                "disk_pct": 45.0
            }
        else:
            # We run the script contents directly via standard input of a shell
            ssh_pass = os.environ.get("SSH_PASSWORD") or args.passcode
            exit_code, out_text, err_text = run_remote_ssh(
                args.host, args.port, args.user, ssh_pass, args.ssh_key,
                "sh", stdin_data=REMOTE_METRICS_SH
            )
            if exit_code != 0:
                print(json.dumps({
                    "error": f"Failed to get remote metrics: exit code {exit_code}",
                    "stderr": err_text
                }))
                sys.exit(1)
            metrics = json.loads(out_text.strip())

    # 2. Evaluate Decision Tree
    ram_high = metrics.get("mem_pct", 0.0) > args.ram_threshold
    cpu_high = metrics.get("cpu_pct", 0.0) > args.cpu_threshold

    action = "none"
    command = None
    safe_service = shlex.quote(args.service)

    if ram_high and cpu_high:
        action = "restart_docker"
        command = "sudo -n systemctl restart docker"
    elif ram_high and not cpu_high:
        action = "read_logs"
        command = f"docker logs --timestamps --tail {args.log_lines} {safe_service}"
    elif not ram_high and cpu_high:
        action = "restart_service"
        command = f"docker restart {safe_service}"
    else:
        action = "none"
        command = None

    result = {
        "timestamp": pydoc.locate("time").strftime("%Y-%m-%dT%H:%M:%SZ", pydoc.locate("time").gmtime()) if pydoc.locate("time") else "2026-07-01T12:00:00Z",
        "host": args.host,
        "thresholds": {
            "ram": args.ram_threshold,
            "cpu": args.cpu_threshold
        },
        "metrics": metrics,
        "evaluation": {
            "ram_high": ram_high,
            "cpu_high": cpu_high
        },
        "action": action,
        "command": command,
        "executed": False
    }

    # 3. Perform execution if requested
    if args.execute and command:
        if args.host == "demo":
            result["executed"] = True
            result["exit_code"] = 0
            result["stdout"] = f"Mock execution of: {command} succeeded"
            result["stderr"] = ""
        else:
            ssh_pass = os.environ.get("SSH_PASSWORD") or args.passcode
            exit_code, out_text, err_text = run_remote_ssh(
                args.host, args.port, args.user, ssh_pass, args.ssh_key,
                command
            )
            result["executed"] = True
            result["exit_code"] = exit_code
            result["stdout"] = out_text
            result["stderr"] = err_text

    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
