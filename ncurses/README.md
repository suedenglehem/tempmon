# ncurses/tempmon.py — terminal viewer for thermal.log

Stdlib-only Python (curses), zero deps. Tails `/var/log/thermal.log`
(the daemon's log) and redraws every 3 s.

    ./tempmon.py                 # default log /var/log/thermal.log
    THERMAL_LOG=... ./tempmon.py # env override, same hook as the daemon
    ./tempmon.py --log PATH --history 40 --interval 3

Shows: cpu.Tctl/Tdie + cpu.W, every gpuN + gpu.W, nvme/disk temps
(whatever the host logs — fields are parsed generically), the last
sample's age with a LIVE/STALE flag, and 8-level sparklines for CPU
and GPU-max over the last N samples (default 40 = 2 min) with
min/max/avg. Color: green <70, yellow 70–84, red ≥85.
`q` quits. Terminal too small degrades gracefully (no crash).
