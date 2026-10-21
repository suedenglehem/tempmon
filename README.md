# tempmon — thermal monitor (daemon + web graph)

Built 2026-10-21. Purpose: forensics for b760one/tr4 (192.168.0.30, AMD 1950X)
repeated hard-power-loss reboots — log all temps + power draw every 3 s so the
last 15 min survive for post-mortem, and graph them on the LAN. The dashboard
names itself `thermal-<hostname>` (real hostname, not hardcoded).

## Contents
- `thermal-daemon` — POSIX sh logger: cpu (AMD k10temp / Intel coretemp —
  fields stay cpu.Tctl=package/control, cpu.Tdie=die/core0), cpu power
  (powercap RAPL "package-*" zone, energy_uj delta between samples → cpu.W;
  works on AMD too — the kernel exposes intel-rapl:0 named package-0),
  gpu (nvidia-smi temps per GPU + power.draw summed over all GPUs → gpu.W),
  all SATA disks (smartctl, needs root), nvme (sysfs hwmon) every 3 s →
  /var/log/thermal.log; self-rotates ×2 (last 15 min); start/stop/rotate
  markers (start marker lists sources incl. the powercap zone).
  Test hooks: THERMAL_LOG / THERMAL_INTERVAL / THERMAL_ROTATE_EVERY.
- `thermal-daemon.service` — systemd unit (Restart=always, runs as root for smartctl).
- `thermal-graph` — stdlib-only Python (ThreadingHTTPServer), zero CDN;
  `/` dashboard (6 stacked canvas panels: cpu temps / cpu power / gpu power
  (total) / nvme / other disk / gpu temps; per-panel units — W on the power
  panels, ° on the temp ones) + `/api/data` JSON. Env: THERMAL_WEB_HOST
  (0.0.0.0), THERMAL_WEB_PORT (9066), THERMAL_WEB_TRIP_C (cpu trip line, 95).
- `thermal-graph.service` — systemd unit (Restart=always, enabled).
- `thermal.txt` — post-mortem inspection notes (what to look for after a death).
- `install.sh` — one-shot installer (Debian/Ubuntu, root): installs
  **smartmontools** (disk temp retrieval), the two programs and their units,
  enables + starts both services, optionally opens the port via ufw, and
  prints the first samples.

## Deployed copies
- b760one (192.168.0.30) — live at /usr/local/sbin/{thermal-daemon,thermal-graph}
  + /etc/systemd/system/*.service; restarted 2026-10-21 with the hostname +
  power changes, verified (15 API series incl. cpu.W/gpu.W, page 200).
- i7 (192.168.0.33) — live, current (redeployed 2026-10-21 via
  `install.sh --trip 100`): dynamic title `thermal-i7`, all 5 panels
  incl. cpu.W (14700K RAPL package-0) + gpu.W (2 GPUs summed), trip 100.

## Install on a fresh box
```
sudo sh install.sh [--trip C] --lan <viewer-ip> [--lan <viewer-ip> ...]
```
- `--trip C` — CPU trip temperature for the dashed line on the cpu panel
  (default 95 = AMD 1950X; 100 for Intel i7-14700K). Written into the graph
  unit as THERMAL_WEB_TRIP_C.
- `--lan IP` (repeatable) — source-restricted ufw rule for the dashboard port;
  only applied when ufw is active (with inactive ufw the port is simply open).
  Without any --lan the script prints the ufw command to run.
- Works on AMD (k10temp) and Intel (coretemp) CPUs; missing sources (no GPU
  driver, no NVMe, disks without a temp attribute) just read NA.
- cpu.W appears when the kernel exposes a powercap RAPL zone named package-*
  (modern Intel; verified on the AMD 1950X). No such zone → field omitted,
  installer prints a note; everything else unaffected.

Manual install (no script):
```
sudo apt-get install -y smartmontools      # disk temp retrieval (smartctl)
sudo cp -a thermal-daemon thermal-graph /usr/local/sbin/
sudo cp -a thermal-daemon.service thermal-graph.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now thermal-daemon thermal-graph
```

## Firewall (source-restricted to the two viewers)
```
sudo ufw allow from 192.168.0.33  to any port 9066 proto tcp   # i7  (Linux boot)
sudo ufw allow from 192.168.0.100 to any port 9066 proto tcp   # ak  (Windows boot)
```
Note: ak and i7 are ONE physical dual-boot machine — only one IP is up at a
time; both rules are needed so whichever boot is live can open the dashboard.

## Watch for (after next sudden shutdown)
- journalctl --list-boots — abrupt end = hard power loss
- /var/log/thermal.log + .1/.2 — cpu.Tdie approaching 95 °C (1950X trip),
  a temp spike, or samples simply stopping (no "daemon stop" marker).
