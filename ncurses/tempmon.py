#!/usr/bin/env python3
"""tempmon.py — live ncurses viewer for /var/log/thermal.log.

Reads the newest CPU/GPU temperatures from the thermal-daemon log and
redraws every 3 s. Stdlib only, zero deps — same rule as the rest of
the tempmon repo. Fields are parsed generically (key=value), so hosts
with 0..n GPUs / nvme / disks render fine.

The CPU / GPU graphs are two-row dot bar charts (· small dot, ● big dot,
filled bottom-up) — twice the height of a plain sparkline, five
quantization levels per column — and their width auto-fits the
terminal, so a bigger terminal shows more history. Memory (used/free/swap, text only — no graph) is not in
the log, so the app polls /proc/meminfo itself on the same tick.

Keys:  q quit
Env:   THERMAL_LOG   log path (default /var/log/thermal.log)
"""
import argparse
import curses
import locale
import os
import re
from datetime import datetime

PREFIX_RE = re.compile(r"^(\d{2}:\d{2}:\d{2})\s+(\d{2}/\d{2}/\d{4})\s+(.*)$")
KV_RE = re.compile(r"([A-Za-z0-9_.]+)=([\d.]+)")
BAR_UNICODE = {0: (" ", " "), 1: (" ", "·"), 2: (" ", "●"), 3: ("·", "●"), 4: ("●", "●")}
BAR_ASCII = {0: (" ", " "), 1: (" ", "."), 2: (" ", "#"), 3: (".", "#"), 4: ("#", "#")}
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
    want = ("MemTotal", "MemFree", "MemAvailable", "SwapTotal", "SwapFree")
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


def series(hist, key):
    return [s.kv[key] for s in hist if key in s.kv]


def series_max(hist, keys):
    """Per-sample max across the given keys (i.e. worst GPU). Skips samples
    that lack every key (rare: source disappearing mid-log)."""
    out = []
    for s in hist:
        vals = [s.kv[k] for k in keys if k in s.kv]
        if vals:
            out.append(max(vals))
    return out


def spark2(vals, width, unicode_ok=True):
    """Two-row dot bar chart (· small dot, ● big dot), 5 levels per column
    — twice the old single-row sparkline's height, but no block glyphs, so
    no font/half-block slivers that render as gaps on some terminals.
    Returns (top_line, bottom_line)."""
    vals = list(vals)[-width:]
    if not vals:
        return "", ""
    lo, hi = min(vals), max(vals)
    table = BAR_UNICODE if unicode_ok else BAR_ASCII
    top, bot = [], []
    for v in vals:
        t = 0.5 if hi == lo else (v - lo) / (hi - lo)
        # floor(t*4 + 0.5), not round(): round() is banker's rounding and
        # makes exact midpoints jitter a level between frames
        t_ch, b_ch = table[int(t * 4 + 0.5)]
        top.append(t_ch)
        bot.append(b_ch)
    return "".join(top), "".join(bot)


def fmt(v):
    return f"{v:.0f}" if float(v).is_integer() else f"{v:.1f}"


def tattr(v, color_ok):
    if not color_ok:
        return curses.A_BOLD
    if v >= 85:
        return curses.color_pair(3) | curses.A_BOLD
    if v >= 70:
        return curses.color_pair(2)
    return curses.color_pair(1)


def init_colors(scr):
    if not curses.has_colors():
        return False
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN, -1)
    curses.init_pair(2, curses.COLOR_YELLOW, -1)
    curses.init_pair(3, curses.COLOR_RED, -1)
    curses.init_pair(4, curses.COLOR_CYAN, -1)
    return True


def draw(scr, log_path, hist_n, interval, color_ok, deg, unicode_ok, mem_now):
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
    win = hist_n if hist_n and hist_n > 0 else max(40, cols - 7)
    hist = samples[-win:]
    gpu_keys = sorted(k for k in kv if k.startswith("gpu") and k != "gpu.W")
    put(2, 1, "CPU", curses.A_BOLD)
    if "cpu.Tctl" in kv:
        put(2, 6, f"{fmt(kv['cpu.Tctl'])}{deg}", tattr(kv["cpu.Tctl"], color_ok))
    if "cpu.Tdie" in kv:
        put(2, 18, f"Tdie {fmt(kv['cpu.Tdie'])}{deg}", tattr(kv["cpu.Tdie"], color_ok))
    if "cpu.W" in kv:
        put(2, 34, f"{fmt(kv['cpu.W'])} W", A_DIM)
    gpu_last = series_max([last], gpu_keys)
    put(3, 1, "GPU", curses.A_BOLD)
    if gpu_last:
        put(3, 6, f"{fmt(gpu_last[0])}{deg}", tattr(gpu_last[0], color_ok))
    if "gpu.W" in kv:
        put(3, 18, f"{fmt(kv['gpu.W'])} W", A_DIM)
    others = {k: v for k, v in kv.items()
              if k.startswith("nvme") or re.match(r"^sd[a-z]$", k)}
    if others:
        put(4, 1, "HDD", curses.A_BOLD)
        put(4, 6, "  ".join(f"{k} {fmt(v)}{deg}" for k, v in sorted(others.items())))

    put(5, 1, "MEM", curses.A_BOLD)
    if mem_now and mem_now.get("MemTotal"):
        total = mem_now["MemTotal"]
        avail = mem_now.get("MemAvailable", mem_now.get("MemFree", 0))
        used = total - avail
        pct = 100.0 * used / total
        put(5, 6, f"used {fmt_kb(used)} of {fmt_kb(total)} ({pct:.0f}%)",
            pct_attr(pct, color_ok))
        put(5, 33, f"free {fmt_kb(avail)}", color_ok and curses.color_pair(4) or curses.A_BOLD)
        st = mem_now.get("SwapTotal", 0)
        if st:
            put(5, 47, f"swap {fmt_kb(st - mem_now.get('SwapFree', 0))}/{fmt_kb(st)}", A_DIM)
    else:
        put(5, 6, "cannot read /proc/meminfo", A_DIM)

    age = (datetime.now() - last.ts).total_seconds()
    put(6, 1, f"sample {last.ts:%H:%M:%S}  {age:.0f}s ago")
    if age > STALE_AFTER:
        put(6, 34, "STALE", curses.color_pair(3) | curses.A_BOLD if color_ok else curses.A_BOLD)
    else:
        put(6, 34, "LIVE", curses.color_pair(4) | curses.A_BOLD if color_ok else curses.A_BOLD)

    def graph(y, label, vals):
        if not vals or y + 2 >= rows:
            return
        top, bot = spark2(vals, cols - 7, unicode_ok)
        attr = tattr(vals[-1], color_ok)
        put(y, 1, label, curses.A_BOLD)
        put(y, 7, top, attr)
        put(y + 1, 7, bot, attr)
        lo, hi = min(vals), max(vals)
        put(y + 2, 7, f"min {fmt(lo)}  max {fmt(hi)}  avg {fmt(sum(vals) / len(vals))}"
                      f"  ({len(vals)})", A_DIM)

    # row 7 stays empty under the "sample ..." line — whitespace before graphs
    graph(8, "CPU", series(hist, "cpu.Tctl"))
    graph(12, "GPU", series_max(hist, gpu_keys))
    put(rows - 1, 0, f" refresh {interval:.0f}s · window {len(hist)} samples", A_DIM)
    scr.noutrefresh()
    curses.doupdate()


def main(scr, log_path, hist_n, interval, deg, unicode_ok):
    curses.curs_set(0)
    scr.timeout(int(interval * 1000))
    color_ok = init_colors(scr)
    mem_now = None
    while True:
        mem_now = read_meminfo() or mem_now  # keep last good values on failure
        draw(scr, log_path, hist_n, interval, color_ok, deg, unicode_ok, mem_now)
        if scr.getch() in (ord("q"), ord("Q")):
            break


def run():
    ap = argparse.ArgumentParser(description="ncurses viewer for thermal.log")
    ap.add_argument("--log", default=os.environ.get("THERMAL_LOG",
                                                    "/var/log/thermal.log"))
    ap.add_argument("--history", type=int, default=None,
                    help="samples in the graph window (default: fit terminal width)")
    ap.add_argument("--interval", type=float, default=3.0,
                    help="refresh seconds (default 3, matches the daemon)")
    args = ap.parse_args()

    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass
    utf8 = locale.getpreferredencoding(False).upper().replace("-", "") == "UTF8"
    deg, spark = ("°C", True) if utf8 else (" C", False)

    try:
        curses.wrapper(main, args.log, args.history, args.interval, deg, spark)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
