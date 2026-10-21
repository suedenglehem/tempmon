# tempmon — b760one thermal monitor (daemon + web graph)

Built 2026-10-21. Purpose: forensics for b760one/tr4 (192.168.0.30, AMD 1950X)
repeated hard-power-loss reboots — log all temps every 3 s so the last 15 min
survive for post-mortem, and graph them on the LAN.

## Contents
- `thermal-daemon` — POSIX sh logger: cpu (k10temp Tctl/Tdie), gpu (nvidia-smi),
  sda-sdf (smartctl, needs root), nvme (sysfs hwmon) every 3 s →
  /var/log/thermal.log; self-rotates ×2 (last 15 min); start/stop/rotate markers.
  Test hooks: THERMAL_LOG / THERMAL_INTERVAL / THERMAL_ROTATE_EVERY.
- `thermal-daemon.service` — systemd unit (Restart=always, runs as root for smartctl).
- `thermal-graph` — stdlib-only Python (ThreadingHTTPServer), zero CDN;
  `/` dashboard (4 stacked canvas panels: cpu / nvme / other disk / gpu) +
  `/api/data` JSON. Env: THERMAL_WEB_HOST (0.0.0.0), THERMAL_WEB_PORT (9066).
- `thermal-graph.service` — systemd unit (Restart=always, enabled).
- `thermal.txt` — post-mortem inspection notes (what to look for after a death).

## Installed on b760one (verified byte-identical to these copies, 2026-10-21)
- /usr/local/sbin/thermal-daemon, /usr/local/sbin/thermal-graph
- /etc/systemd/system/thermal-daemon.service, /etc/systemd/system/thermal-graph.service

## Install on a fresh box
```
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
