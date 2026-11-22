#!/usr/bin/env python3
"""tempmon.py — live ncurses viewer for /var/log/thermal.log.

Reads the newest CPU/GPU temperatures from the thermal-daemon log and
redraws every 3 s. Stdlib only, zero deps — same rule as the rest of
the tempmon repo. Fields are parsed generically (key=value), so hosts
with 0..n GPUs / nvme / disks render fine.

Keys:  q  quit
Env:   THERMAL_LOG   log path (default /var/log/thermal.log)
"""
import argparse
import curses
import locale
import os
import re
import sys
import time
from datetime import datetime

PREFIX_RE = re.compile(r"^(\d{2}:\d{2}:\d{2})\s+(\d{2}/\d{2}/\d{4})\s+(.*)$")
KV_RE = re.compile(r"([A-Za-z0-9_.]+)=([\d.]+)")
SPARK_UNICODE = "▁▂▃▄▅▆▇█"
SPARK_ASCII = " .:-=+*#"
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


def series(hist, key):
    return [s.kv[key] for s in hist if key in s.kv]


def series_max(hist, keys):
    vals = [[s.kv[k] for k in keys if k in s.kv] for s in hist]
    return [max(v) for v in vals if v]


def sparkline(vals, width, chars):
    vals = list(vals)[-width:]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    rng = hi - lo
    n = len(chars) - 1
    out = []
    for v in vals:
        i = n // 2 if rng == 0 else int((v - lo) / rng * n + 0.5)
        out.append(chars[i])
    return "".join(out)


def main(stdscr, log_path, hist_n, interval, deg, spark_chars):
    curses.curs_set(0)
    stdscr.timeout(int(interval * 1000))
    color_ok = curses.has_colors()
    if color_ok:
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_GREEN, -1)
        curses.init_pair(2, curses.COLOR_YELLOW, -1)
        curses.init_pair(3, curses.COLOR_RED, -1)
        curses.init_pair(4, curses.COLOR_CYAN, -1)

    def tattr(t):
        if not color_ok:
            return curses.A_BOLD
        if t >= 85:
            return curses.color_pair(3) | curses.A_BOLD
        if t >= 70:
            return curses.color_pair(2)
        return curses.color_pair(1)

    def fmt(t):
        return f"{t:.0f}{deg}" if float(t).is_integer() else f"{t:.1f}{deg}"

    while True:
        stdscr.erase()
        rows, cols = stdscr.getmaxyx()

        def put(y, x, s, attr=0):
            if y < 0 or y >= rows or x >= cols - 1:
                return
            try:
                stdscr.addnstr(y, x, s, max(0, cols - 1 - x), attr)
            except curses.error:
                pass

        samples = read_samples(log_path)
        put(0, 0, f" tempmon · {log_path}")
        put(0, max(0, cols - 9), "q quit", curses.A_DIM)
        put(1, 0, ("─" if deg == "°C" else "-") * (cols - 1))

        if samples is None:
            put(3, 1, f"cannot open {log_path}", curses.A_BOLD)
            put(4, 1, "waiting for the thermal-daemon log…", curses.A_DIM)
            stdscr.noutrefresh()
            curses.doupdate()
            if stdscr.getch() in (ord("q"), ord("Q")):
                return
            continue

        if not samples:
            put(3, 1, "no samples in log yet", curses.A_BOLD)
            stdscr.noutrefresh()
            curses.doupdate()
            if stdscr.getch() in (ord("q"), ord("Q")):
                return
            continue

        last = samples[-1]
        hist = samples[-hist_n:]
        kv = last.kv

        y = 2
        tctl, tdie, cw = kv.get("cpu.Tctl"), kv.get("cpu.Tdie"), kv.get("cpu.W")
        if tctl is not None:
            put(y, 1, "CPU ")
            put(y, 5, fmt(tctl), tattr(tctl))
            if tdie is not None:
                put(y, 14, f"Tdie {fmt(tdie)}", tattr(tdie))
            if cw is not None:
                put(y, 31, f"{cw:.1f} W", curses.A_DIM)
            y += 1

        gpu_keys = sorted(k for k in kv if k.startswith("gpu") and k != "gpu.W")
        if gpu_keys:
            x = 5
            put(y, 1, "GPU")
            for k in gpu_keys:
                put(y, x, f"{fmt(kv[k])}", tattr(kv[k]))
                x += 9
            gw = kv.get("gpu.W")
            if gw is not None:
                put(y, x, f"{gw:.1f} W", curses.A_DIM)
            y += 1

        other = sorted(k for k in kv if k.startswith(("nvme", "sd")))
        if other:
            put(y, 1, "HDD " + "  ".join(f"{k} {fmt(kv[k])}" for k in other),
                curses.A_DIM)
            y += 1

        age = (datetime.now() - last.ts).total_seconds()
        stale = age > STALE_AFTER
        put(y, 1, f"sample {last.ts:%H:%M:%S}  {age:.0f}s ago",
            curses.color_pair(3) | curses.A_BOLD if (stale and color_ok)
            else (curses.A_BOLD if stale else curses.A_DIM))
        put(y, 34, "STALE — daemon not writing?" if stale else "LIVE",
            curses.color_pair(3) | curses.A_BOLD if (stale and color_ok)
            else curses.color_pair(4) if color_ok else curses.A_DIM)
        y += 2

        def stats(vals):
            if not vals:
                return ""
            return (f"min {min(vals):.1f}  max {max(vals):.1f}  "
                    f"avg {sum(vals) / len(vals):.1f}  ({len(vals)})")

        cpu_s = series(hist, "cpu.Tctl")
        if cpu_s:
            put(y, 1, "CPU ")
            put(y, 5, sparkline(cpu_s, cols - 6, spark_chars),
                tattr(cpu_s[-1]))
            put(y + 1, 5, stats(cpu_s), curses.A_DIM)
            y += 2

        gpu_s = series_max(hist, gpu_keys)
        if gpu_s:
            label = "GPU " if len(gpu_keys) == 1 else "GPUmax"
            put(y, 1, label)
            put(y, 7, sparkline(gpu_s, cols - 8, spark_chars),
                tattr(gpu_s[-1]))
            put(y + 1, 7, stats(gpu_s), curses.A_DIM)
            y += 2

        put(rows - 1, 0, f" refresh {interval:.0f}s · window {hist_n} samples",
            curses.A_DIM)
        stdscr.noutrefresh()
        curses.doupdate()

        key = stdscr.getch()  # blocks up to `interval`, -1 on timeout
        if key in (ord("q"), ord("Q")):
            return


def run():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", default=os.environ.get("THERMAL_LOG",
                                                    "/var/log/thermal.log"))
    ap.add_argument("--history", type=int, default=40,
                    help="samples in the sparkline window (default 40 ≈ 2 min)")
    ap.add_argument("--interval", type=float, default=3.0,
                    help="refresh seconds (default 3, matches the daemon)")
    args = ap.parse_args()

    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass
    utf8 = locale.getpreferredencoding(False).upper().replace("-", "") == "UTF8"
    deg, spark = ("°C", SPARK_UNICODE) if utf8 else (" C", SPARK_ASCII)

    try:
        curses.wrapper(main, args.log, args.history, args.interval, deg, spark)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
