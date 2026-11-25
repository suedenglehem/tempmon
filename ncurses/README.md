# ncurses/tempmon.py — terminal viewer for thermal.log

Stdlib-only Python (curses), zero deps. Tails `/var/log/thermal.log`
(the daemon's log) and redraws every 3 s.

    ./tempmon.py                 # default log /var/log/thermal.log
    THERMAL_LOG=... ./tempmon.py # env override, same hook as the daemon
    ./tempmon.py --log PATH --interval 3
    ./tempmon.py -hw             # with -hw: device names next to the labels
    THERMAL_HW_FILE=... -hw      # override inventory path
    ../thermal_nc                # launcher (repo root) = tempmon.py -hw

Shows: cpu.Tctl/Tdie + cpu.W, every gpuN with its own watts (gpu0.W..)
and the fleet total ΣW, nvme/disk temps (whatever the host logs —
fields are parsed generically), MEM used / free (totals, from
/proc/meminfo — no swap), and the last sample's age with a LIVE/STALE
flag on the bottom status line (with the refresh rate).

Blob board: every sensor (CPU and each GPU) also gets one big rounded
tile with the live temp inside, colored by temperature — deep blue
<45 °C, green 45–59, violet 60–69, yellow 70–89, dark red 90+. The
temp text in the rows uses the same grading. 256-color terminals get
the deep blue, the saturated violet and the dark red; low-color
terminals stand violet in as magenta, so all five bands stay
distinguishable down to the basic 8 colors; mono chrome falls back
to A_REVERSE.

With -hw: names come from the daemon's own inventory
(/var/lib/thermal/hardware.json) — CPU model, GPU model, disk vendor.
Names are squeezed into a 10-col whole-word column next to the CPU /
GPU / HDD labels and values shift right to match. GPUs get ONE PER LINE
either way (gpu0..gpuN, each with its own name and its own watts;
without -hw the line still carries its knob, so "53/45/48" is never
ambiguous). Disks get ONE DEVICE PER LINE with -hw (knob + its own
name; "?" when the inventory lacks it), so every hw id is visible —
without -hw they share the compact single row. No/legacy json -> warns
once and runs without names.

Memory is not in the log — the app polls /proc/meminfo itself every
tick (text row only). MEM% green <60, yellow 60–80, red ≥80. `q`
quits. Terminal too small skips the blob board (no crash).
