#!/usr/bin/env python3
"""tempmon.py — live ncurses viewer for /var/log/thermal.log.

Reads the newest CPU/GPU temperatures from the thermal-daemon log and
redraws every 3 s. Stdlib only, zero deps — same rule as the rest of
the tempmon repo. Fields are parsed generically (key=value), so hosts
with 0..n GPUs / nvme / disks render fine.

Temperatures are presented as one big colored blob per sensor (CPU and
every GPU): the live value inside a filled rounded tile, color-graded
deep blue <45 °C, green 45–59, yellow 60–69, orange 70–89, red 90+.
Memory (used/free,
text only — no graph) is not in the log, so the app polls
/proc/meminfo itself on the same tick. The bottom status line shows the
last sample's age with a LIVE/STALE flag.

With -hw / --hw: recovers device names from the daemon's hardware
inventory (THERMAL_HW_FILE, default /var/lib/thermal/hardware.json) and
shows them in a 10-col column next to the CPU/GPU/HDD labels.

Keys:  q quit
Env:   THERMAL_LOG       log path (default /var/log/thermal.log)
       THERMAL_HW_FILE   hardware json (used with -hw)
"""
import argparse
import curses
import json
import locale
import os
import re
import sys
from datetime import datetime

PREFIX_RE = re.compile(r"^(\d{2}:\d{2}:\d{2})\s+(\d{2}/\d{2}/\d{4})\s+(.*)$")
KV_RE = re.compile(r"([A-Za-z0-9_.]+)=([\d.]+)")
# Temperature bands (°C): <45 deep blue, 45–59 green, 60–69 yellow,
# 70–89 orange, 90+ red. Pair ids 10..14 = text colors, 20..24 = blob
# (fg,bg) fills, assigned in init_colors. The 256-color ids give the
# "deep" blue and a true orange; the 16-color list is the fallback.
BAND_HI = (45.0, 60.0, 70.0, 90.0)
TEXT_256 = (27, 46, 220, 208, 196)
TEXT_16 = (curses.COLOR_BLUE, curses.COLOR_GREEN, curses.COLOR_YELLOW,
           curses.COLOR_RED, curses.COLOR_RED)
BLOB_256 = ((231, 17), (231, 22), (16, 226), (16, 208), (231, 160))
BLOB_16 = ((curses.COLOR_WHITE, curses.COLOR_BLUE),
           (curses.COLOR_WHITE, curses.COLOR_GREEN),
           (curses.COLOR_BLACK, curses.COLOR_YELLOW),
           (curses.COLOR_BLACK, curses.COLOR_RED),
           (curses.COLOR_BLACK, curses.COLOR_RED))
STALE_AFTER = 10.0  # daemon writes every 3 s; >10 s old → stale


class Sample:
    __slots__ = ("ts", "kv")

    def __init__(self, hms, dmy, rest):
        self.ts = datetime.strptime(f"{hms} {dmy}", "%H:%M:%S %d/%m/%Y")
        self.kv = {k: float(v) for k, v in KV_RE.findall(rest)}


def read_samples(path, cap=4000):
    """Parse the whole log (it self-rotates to ~15 min, so this is cheap).
    Returns list of Sample, [] if nothing parseable, None if unreadable."""
    try:
        with open(path, "r", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return None
    out = []
    for ln in lines[-cap:]:
        m = PREFIX_RE.match(ln.strip())
        if not m:
            continue  # rotation/start markers etc.
        try:
            out.append(Sample(m.group(1), m.group(2), m.group(3)))
        except ValueError:
            pass
    return out


def read_meminfo():
    """Poll /proc/meminfo ourselves — thermal.log has no memory data.
    Returns dict of the fields we care about (kB), or None on failure."""
    want = ("MemTotal", "MemFree", "MemAvailable")
    try:
        with open("/proc/meminfo") as f:
            vals = {}
            for ln in f:
                k = ln.split(":", 1)[0]
                if k in want:
                    vals[k] = int(ln.split()[1])
        return vals or None
    except (OSError, ValueError, IndexError):
        return None


def fmt_kb(kb):
    if kb is None:
        return "-"
    g = kb / 1048576.0
    if g >= 10:
        return f"{g:.0f}G"
    if g >= 1:
        return f"{g:.1f}G"
    return f"{kb / 1024:.0f}M"


def pct_attr(p, color_ok):
    if not color_ok:
        return curses.A_BOLD
    if p >= 80:
        return curses.color_pair(3) | curses.A_BOLD
    if p >= 60:
        return curses.color_pair(2)
    return curses.color_pair(1)


def read_hw(path):
    """thermal-daemon's knob->device inventory; None if absent/unreadable."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def col(s, w=10):
    """Squeeze a device name into the fixed 10-col name column: keep whole
    leading words while they fit, hard-cut only as a last resort."""
    s = re.sub(r"\s+", " ", str(s)).strip()
    out = ""
    for p in s.split(" "):
        cand = f"{out} {p}".strip()
        if len(cand) > w and out:
            break
        out = cand
    return (out or s)[:w]


def cpu_name(hw):
    s = re.sub(r"\((R|TM|C)\)", "", hw.get("cpu") or "")
    toks = [t for t in re.split(r"\s+", s) if t]
    noise = re.compile(r"(?i)core|gen|processor|bit|with|graphics|amd|oem")
    dig = [t for t in toks if re.search(r"\d", t) and not noise.search(t)
           and not re.search(r"\d(st|nd|rd|th)$", t)]
    alnum = [t for t in dig if re.search(r"[A-Za-z]", t)]
    dash = [t for t in alnum if "-" in t]
    pick = (dash or alnum or dig or toks[-1:])[0]
    return pick[:10]


def gpu_name(n):
    n = re.sub(r"NVIDIA|GeForce|Graphics|Card|Advanced Micro Devices|0x\w+",
               "", str(n), flags=re.I)
    return col(n)


def disk_names(hw):
    """knob -> short name; device nvme0n1 maps to log knob nvme0, sda stays."""
    out = {}
    for d in (hw or {}).get("disks", []):
        nm = f"{d.get('vendor', '')} {d.get('model', '')}".strip() or d.get("name", "")
        knob = re.sub(r"n\d+$", "", str(d.get("name", "")))
        if knob:
            out.setdefault(knob, col(nm))
    return out


def fmt(v):
    return f"{v:.0f}" if float(v).is_integer() else f"{v:.1f}"


def band(v):
    for i, hi in enumerate(BAND_HI):
        if v < hi:
            return i
    return len(BAND_HI)  # 90+ → red


def tattr(v, color_ok):
    if not color_ok:
        return curses.A_BOLD
    b = band(v)
    return curses.color_pair(10 + b) | (curses.A_BOLD if b == 4 else 0)


def blob_attr(v, color_ok):
    if not color_ok:
        return curses.A_REVERSE | curses.A_BOLD
    return curses.color_pair(20 + band(v))


def init_colors(scr):
    if not curses.has_colors():
        return False
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN, -1)
    curses.init_pair(2, curses.COLOR_YELLOW, -1)
    curses.init_pair(3, curses.COLOR_RED, -1)
    curses.init_pair(4, curses.COLOR_CYAN, -1)
    n = curses.COLORS >= 256
    for i in range(5):
        curses.init_pair(10 + i, (TEXT_256 if n else TEXT_16)[i], -1)
        fg, bg = (BLOB_256 if n else BLOB_16)[i]
        curses.init_pair(20 + i, fg, bg)
    return True


def draw(scr, log_path, interval, color_ok, deg, unicode_ok, mem_now, hw):
    scr.erase()
    rows, cols = scr.getmaxyx()

    def put(y, x, s, attr=0):
        if 0 <= y < rows and 0 <= x < cols:
            try:
                scr.addnstr(y, x, s, max(0, cols - 1 - x), attr)
            except curses.error:
                pass

    A_DIM = curses.A_DIM if hasattr(curses, "A_DIM") else curses.A_BOLD
    put(0, 0, f" tempmon — {log_path} ", curses.A_BOLD)
    put(0, max(0, cols - 8), "q quit", A_DIM)
    put(1, 0, ("─" if unicode_ok else "-") * (cols - 1), A_DIM)

    samples = read_samples(log_path)
    if samples is None:
        put(3, 2, f"cannot open {log_path}", curses.A_BOLD)
        put(4, 2, "waiting for the thermal-daemon log...", A_DIM)
        scr.noutrefresh()
        curses.doupdate()
        return
    if not samples:
        put(3, 2, "log readable but no samples yet", curses.A_BOLD)
        scr.noutrefresh()
        curses.doupdate()
        return

    last = samples[-1]
    kv = last.kv
    # -hw: 10-col device-name column after the labels, values shift right
    X = 12 if hw else 0

    def name_col(y, name):
        if hw:
            put(y, 6, (name or "")[:10].ljust(10), A_DIM)

    gpu_keys = sorted(k for k in kv if re.fullmatch(r"gpu\d+", k))  # temps only
    others = {k: v for k, v in kv.items()
              if k.startswith("nvme") or re.match(r"^sd[a-z]$", k)}
    if hw:
        cpu_n = cpu_name(hw)
        gmap = {g.get("knob"): gpu_name(g.get("name", "")) for g in hw.get("gpus", [])}
        dmap = disk_names(hw)
    else:
        dmap = {}
        gmap = {}
        cpu_n = ""

    row = 2
    put(row, 1, "CPU", curses.A_BOLD)
    name_col(row, cpu_n)
    if "cpu.Tctl" in kv:
        put(row, 6 + X, f"{fmt(kv['cpu.Tctl'])}{deg}", tattr(kv["cpu.Tctl"], color_ok))
    if "cpu.Tdie" in kv:
        put(row, 18 + X, f"Tdie {fmt(kv['cpu.Tdie'])}{deg}", tattr(kv["cpu.Tdie"], color_ok))
    if "cpu.W" in kv:
        put(row, 34 + X, f"{fmt(kv['cpu.W'])} W", A_DIM)
    row += 1
    # one GPU per line: tr4 logs gpu0..gpu2 and "53/45/48°C" says nothing
    if gpu_keys:
        # gpuN.W (per-card watts) when the daemon logs it; gpu.W = fleet total
        percard = any(f"{k}.W" in kv for k in gpu_keys)
        sumtag = "Σ" if unicode_ok else "tot "
        for i, k in enumerate(gpu_keys):
            if i == 0:
                put(row, 1, "GPU", curses.A_BOLD)
                if "gpu.W" in kv:
                    if not percard:  # old logs: fleet sum had no per-card row
                        gtxt = f"{k} {fmt(kv[k])}{deg}"
                        put(row, max(22 + X, 6 + X + len(gtxt) + 3),
                            f"{fmt(kv['gpu.W'])} W", A_DIM)
                    elif len(gpu_keys) > 1:
                        put(row, 34 + X, f"{sumtag}{fmt(kv['gpu.W'])} W", A_DIM)
            if hw:
                put(row, 6, (gmap.get(k) or "?").ljust(10), A_DIM)
            put(row, 6 + X, f"{k} {fmt(kv[k])}{deg}", tattr(kv[k], color_ok))
            if percard and f"{k}.W" in kv:
                put(row, 22 + X, f"{fmt(kv[f'{k}.W'])} W", A_DIM)
            row += 1
    else:
        put(row, 1, "GPU", curses.A_BOLD)
        name_col(row, "")
        row += 1
    if hw and others:
        # -hw: one device per line — every knob gets its own hw-name row
        for i, (k, v) in enumerate(sorted(others.items())):
            if i == 0:
                put(row, 1, "HDD", curses.A_BOLD)
            put(row, 6, (dmap.get(k) or "?").ljust(10), A_DIM)
            put(row, 6 + X, f"{k} {fmt(v)}{deg}")
            row += 1
    elif others:
        put(row, 1, "HDD", curses.A_BOLD)
        put(row, 6 + X, "  ".join(f"{k} {fmt(v)}{deg}" for k, v in sorted(others.items())))
        row += 1

    put(row, 1, "MEM", curses.A_BOLD)
    if mem_now and mem_now.get("MemTotal"):
        total = mem_now["MemTotal"]
        avail = mem_now.get("MemAvailable", mem_now.get("MemFree", 0))
        used = total - avail
        pct = 100.0 * used / total
        put(row, 6 + X, f"used {fmt_kb(used)} of {fmt_kb(total)} ({pct:.0f}%)",
            pct_attr(pct, color_ok))
        put(row, 33 + X, f"free {fmt_kb(avail)}", color_ok and curses.color_pair(4) or curses.A_BOLD)
    else:
        put(row, 6 + X, "cannot read /proc/meminfo", A_DIM)
    row += 1

    row += 2  # blank line under MEM before the blob board

    # blob board: one big rounded tile per sensor with the live temp
    # inside, filled in the band's color (deep blue cold → red hot)
    board = []
    if "cpu.Tctl" in kv:
        board.append(("cpu", kv["cpu.Tctl"]))
    for k in gpu_keys:
        board.append((k.upper(), kv[k]))
    bx = 2
    if row + 3 < rows - 1:  # the whole board or nothing (status line free)
        for lab, v in board:
            t = f"{fmt(v)}°C" if unicode_ok else f"{fmt(v)}C"
            w = max(6, len(t) + 2)
            if bx + w + 2 < cols - 1:
                a = blob_attr(v, color_ok)
                if unicode_ok:
                    put(row, bx, "╭" + "─" * w + "╮", a)
                    put(row + 1, bx, "│" + t.center(w + 2) + "│", a)
                    put(row + 2, bx, "╰" + "─" * w + "╯", a)
                else:
                    put(row, bx, "+" + "-" * w + "+", a)
                    put(row + 1, bx, "|" + t.center(w + 2) + "|", a)
                    put(row + 2, bx, "+" + "-" * w + "+", a)
                put(row + 3, bx + max(0, (w + 2 - len(lab)) // 2), lab, A_DIM)
            bx += w + 6

    # status line: very bottom of the screen (board skips before it)
    age = (datetime.now() - last.ts).total_seconds()
    put(rows - 1, 1, f"sample {last.ts:%H:%M:%S}  {age:.0f}s ago")
    if age > STALE_AFTER:
        put(rows - 1, 34 + X, "STALE", curses.color_pair(3) | curses.A_BOLD if color_ok else curses.A_BOLD)
    else:
        put(rows - 1, 34 + X, "LIVE", curses.color_pair(4) | curses.A_BOLD if color_ok else curses.A_BOLD)
    put(rows - 1, max(0, cols - 11), f"refresh {interval:.0f}s", A_DIM)
    scr.noutrefresh()
    curses.doupdate()


def main(scr, log_path, interval, deg, unicode_ok, hw):
    curses.curs_set(0)
    scr.timeout(int(interval * 1000))
    color_ok = init_colors(scr)
    mem_now = None
    while True:
        mem_now = read_meminfo() or mem_now  # keep last good values on failure
        draw(scr, log_path, interval, color_ok, deg, unicode_ok,
             mem_now, hw)
        if scr.getch() in (ord("q"), ord("Q")):
            break


def run():
    ap = argparse.ArgumentParser(description="ncurses viewer for thermal.log")
    ap.add_argument("--log", default=os.environ.get("THERMAL_LOG",
                                                    "/var/log/thermal.log"))
    ap.add_argument("--interval", type=float, default=3.0,
                    help="refresh seconds (default 3, matches the daemon)")
    ap.add_argument("-hw", "--hw", action="store_true",
                    help="show hardware names in a 10-col next to the labels")
    ap.add_argument("--hw-file", default=os.environ.get("THERMAL_HW_FILE",
                                                        "/var/lib/thermal/hardware.json"),
                    help="hardware inventory json (thermal-daemon's, world-readable)")
    args = ap.parse_args()

    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass
    utf8 = locale.getpreferredencoding(False).upper().replace("-", "") == "UTF8"
    deg, unicode_ok = ("°C", True) if utf8 else (" C", False)

    # -hw: names are loaded once — the daemon rewrites the file only at boot
    hw = read_hw(args.hw_file) if args.hw else None
    if args.hw and hw is None:
        print(f"warning: -hw given but {args.hw_file} unreadable — no names",
              file=sys.stderr)

    try:
        curses.wrapper(main, args.log, args.interval, deg, unicode_ok, hw)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
