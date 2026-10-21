#!/bin/sh
# install.sh — install tempmon (thermal-daemon + thermal-graph) on Debian/Ubuntu.
#
# Usage:
#   sudo sh install.sh [--lan IP]...
#
#   --lan IP   open the dashboard port to IP via ufw (repeatable). Applied only
#              when ufw is active; without --lan the command to run is printed.
#   --trip C   CPU trip temperature for the dashboard's dashed line (default
#              95 for the AMD 1950X; e.g. 100 for an Intel i7-14700K).
#
# Installs:
#   - smartmontools (smartctl) — disk temp retrieval (SATA disks; NVMe temps
#     need no extra tool, they come from sysfs hwmon)
#   - /usr/local/sbin/thermal-daemon + /etc/systemd/system/thermal-daemon.service
#   - /usr/local/sbin/thermal-graph  + /etc/systemd/system/thermal-graph.service
# Enables and starts both services, then shows the first samples.
# GPU temps/power need the NVIDIA driver (nvidia-smi); without it the gpu
# fields read NA and everything else works.
# CPU power (cpu.W) comes from the powercap RAPL "package-*" zone — modern
# Intel, and AMD too (the kernel exposes intel-rapl:0 named package-0 on the
# 1950X); no extra package. If the zone is absent the field is omitted and a
# note is printed at the end.

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
WEB_PORT=9066

LAN_IPS=""
TRIP_C=95
while [ $# -gt 0 ]; do
    case "$1" in
        --lan)
            [ $# -ge 2 ] || { echo "--lan needs an IP" >&2; exit 1; }
            LAN_IPS="$LAN_IPS $2"; shift ;;
        --trip)
            [ $# -ge 2 ] || { echo "--trip needs a value" >&2; exit 1; }
            TRIP_C="$2"; shift ;;
        *)
            echo "unknown argument: $1" >&2; exit 1 ;;
    esac
    shift
done

if [ "$(id -u)" != 0 ]; then
    echo "run as root: sudo sh install.sh" >&2
    exit 1
fi

# ---- 1. disk temp tooling --------------------------------------------------
if ! command -v smartctl >/dev/null 2>&1; then
    command -v apt-get >/dev/null 2>&1 || {
        echo "smartctl missing and apt-get not found — install smartmontools manually" >&2
        exit 1
    }
    echo "installing smartmontools (disk temp retrieval)..."
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq smartmontools
fi
echo "smartctl: $(command -v smartctl)"

# nudge the CPU hwmon driver (AMD k10temp / Intel coretemp); the daemon
# tolerates absence (cpu fields = NA)
if [ -d /sys/class/hwmon ] && ! grep -qsE '^(k10temp|coretemp)$' /sys/class/hwmon/*/name 2>/dev/null; then
    modprobe k10temp 2>/dev/null || modprobe coretemp 2>/dev/null || true
fi

# ---- 2. files ---------------------------------------------------------------
for f in thermal-daemon thermal-graph thermal-daemon.service thermal-graph.service; do
    [ -f "$SCRIPT_DIR/$f" ] || {
        echo "missing $SCRIPT_DIR/$f — run install.sh from the tempmon directory" >&2
        exit 1
    }
done
# pin the cpu trip line in the graph unit (idempotent full-line replace)
sed -i "s|^Environment=.*|Environment=THERMAL_WEB_HOST=0.0.0.0 THERMAL_WEB_PORT=$WEB_PORT THERMAL_WEB_TRIP_C=$TRIP_C|" \
    "$SCRIPT_DIR/thermal-graph.service"
install -m 755 "$SCRIPT_DIR/thermal-daemon" /usr/local/sbin/thermal-daemon
install -m 755 "$SCRIPT_DIR/thermal-graph" /usr/local/sbin/thermal-graph
install -m 644 "$SCRIPT_DIR/thermal-daemon.service" /etc/systemd/system/thermal-daemon.service
install -m 644 "$SCRIPT_DIR/thermal-graph.service" /etc/systemd/system/thermal-graph.service

# ---- 3. services ------------------------------------------------------------
# enable + restart (NOT enable --now): --now only STARTS stopped services,
# so an upgrade with the services already running would keep the old code
# live and silently ship the new binaries without ever using them.
systemctl daemon-reload
systemctl enable thermal-daemon.service thermal-graph.service
systemctl restart thermal-daemon.service thermal-graph.service

# ---- 4. firewall (optional) --------------------------------------------------
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
    if [ -n "$LAN_IPS" ]; then
        for ip in $LAN_IPS; do
            ufw allow from "$ip" to any port "$WEB_PORT" proto tcp
        done
        echo "ufw: dashboard port $WEB_PORT opened for:$LAN_IPS"
    else
        echo "NOTE: ufw is active — open the dashboard with, e.g.:"
        echo "      ufw allow from <viewer-ip> to any port $WEB_PORT proto tcp"
    fi
fi

# ---- 5. verify ---------------------------------------------------------------
systemctl is-active --quiet thermal-daemon.service
systemctl is-active --quiet thermal-graph.service
sleep 4   # let a couple of 3s samples land
echo
echo "--- last thermal samples ---"
tail -n 3 /var/log/thermal.log
echo
if tail -n 10 /var/log/thermal.log | grep -q ' cpu.W='; then
    echo "power: cpu.W (powercap) and gpu.W (nvidia-smi) present"
else
    echo "NOTE: no cpu.W in the samples — no powercap RAPL package zone"
    echo "      found, so CPU power is omitted (everything else is fine)"
fi
echo
echo "tempmon installed and running. Dashboard: http://<this-host-ip>:$WEB_PORT"
