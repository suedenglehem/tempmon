# ncurses/tempmon.py — terminal viewer for thermal.log

Stdlib-only Python (curses), zero deps. Tails `/var/log/thermal.log`
(the daemon's log) and redraws every 3 s.

    ./tempmon.py                 # default log /var/log/thermal.log
    THERMAL_LOG=... ./tempmon.py # env override, same hook as the daemon
    ./tempmon.py --log PATH --history N --interval 3

The graphs auto-fit the terminal width (or pin the history window
with --history N), so a bigger terminal shows more history.

Shows: cpu.Tctl/Tdie + cpu.W, every gpuN + gpu.W, nvme/disk temps
(whatever the host logs — fields are parsed generically), MEM used /
free / swap (all totals, from /proc/meminfo), the last sample's age
with a LIVE/STALE flag, and two-row dot bar graphs — · small dot,
● big dot, five levels per column, twice a sparkline's height,
auto-fit to terminal width — for CPU and GPU-max, each with min/max/avg.
Memory is not in the log — the app polls /proc/meminfo itself every
tick (text row only, no memory graph). Colors: temps green <70, yellow
70–84,
red ≥85; MEM% green <60, yellow 60–80, red ≥80. `q` quits. Terminal
too small degrades gracefully (no crash).
