# ncurses/tempmon.py — terminal viewer for thermal.log

Stdlib-only Python (curses), zero deps. Tails `/var/log/thermal.log`
(the daemon's log) and redraws every 3 s.

    ./tempmon.py                 # default log /var/log/thermal.log
    THERMAL_LOG=... ./tempmon.py # env override, same hook as the daemon
    ./tempmon.py --log PATH --history N --interval 3
    ./tempmon.py -hw             # pro: device names next to the labels
    THERMAL_HW_FILE=... -hw      # override inventory path

The graphs auto-fit the terminal width (or pin the history window
with --history N), so a bigger terminal shows more history.

Pro mode (-hw): names come from the daemon's own inventory
(/var/lib/thermal/hardware.json) — CPU model, GPU model, disk vendor.
Names are squeezed into a 10-col whole-word column next to the CPU /
GPU / HDD labels and values shift right to match. GPUs get ONE PER LINE
(gpu0..gpuN, each with its own name and its own color; plain mode also
labels each line with its knob, so "53/45/48" is never ambiguous).
Disks get ONE DEVICE PER LINE in pro mode (knob + its own name; "?"
when the inventory lacks it), so every hw id is visible — plain mode
keeps the compact single row. No/legacy json -> warns once and runs
plain.

Shows: cpu.Tctl/Tdie + cpu.W, every gpuN + gpu.W, nvme/disk temps
(whatever the host logs — fields are parsed generically), MEM used /
free (totals, from /proc/meminfo — no swap), the last sample's age with
a LIVE/STALE flag on the bottom status line (with the refresh rate),
and two-row dot bar graphs — · small dot,
● big dot, five levels per column, twice a sparkline's height,
auto-fit to terminal width — for CPU and GPU-max, each with min/max/avg.
Memory is not in the log — the app polls /proc/meminfo itself every
tick (text row only, no memory graph). Colors: temps green <70, yellow
70–84,
red ≥85; MEM% green <60, yellow 60–80, red ≥80. `q` quits. Terminal
too small degrades gracefully (no crash).
