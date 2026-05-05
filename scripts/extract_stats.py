#!/usr/bin/env python3
"""
extract_stats.py — Pull comparison metrics out of one or more m5out/stats.txt
files for the Spandex-TU vs GPU_VIPER evaluation.

Usage:
    python3 scripts/extract_stats.py m5out_spandex_square m5out_viper_square
    python3 scripts/extract_stats.py --csv results/comparison.csv \\
        m5out_spandex_*/  m5out_viper_*/

Output (stdout): a human-readable comparison table.
Output (--csv):  a row per (protocol, benchmark) with all key metrics.

The script is read-only on the gem5 side — it only parses stats.txt files.
Robust to missing controllers (e.g. Spandex runs have no tcc_cntrl, VIPER
runs do).
"""

import argparse
import csv
import re
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_STAT_LINE = re.compile(
    r"^([A-Za-z_][\w.:]*?)\s+([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
)


def parse_stats(stats_path: Path) -> dict[str, float]:
    """Parse stats.txt into a flat name → numeric-value dict.

    Lines are 'name value # comment' or 'name nan # comment' or histograms
    we ignore. We only keep scalar numeric stats.
    """
    out: dict[str, float] = {}
    if not stats_path.is_file():
        return out
    with stats_path.open() as f:
        for line in f:
            m = _STAT_LINE.match(line)
            if not m:
                continue
            name, raw = m.group(1), m.group(2)
            try:
                out[name] = float(raw)
            except ValueError:
                pass
    return out


# ---------------------------------------------------------------------------
# Per-controller helpers
# ---------------------------------------------------------------------------

def _sum_indexed(stats: dict[str, float], prefix: str, suffix: str,
                 max_index: int = 64) -> tuple[float, int]:
    """Sum stats[f"{prefix}{i}{suffix}"] for i = 0,1,... until missing.
    Returns (total, count_of_present)."""
    total = 0.0
    n = 0
    for i in range(max_index):
        key = f"{prefix}{i}{suffix}"
        if key in stats:
            total += stats[key]
            n += 1
        elif i > 0:
            break  # contiguous indexing assumption
    return total, n


# ---------------------------------------------------------------------------
# Metric extraction
# ---------------------------------------------------------------------------

def extract(m5out_dir: Path) -> dict[str, Any]:
    stats_path = m5out_dir / "stats.txt"
    raw = parse_stats(stats_path)
    out: dict[str, Any] = {
        "m5out": str(m5out_dir),
        "stats_present": stats_path.is_file(),
    }
    if not raw:
        return out

    # Top-level
    out["simSeconds"] = raw.get("simSeconds")
    out["simTicks"] = raw.get("simTicks")
    out["hostSeconds"] = raw.get("hostSeconds")

    # Network message counts
    msg_pref = "system.ruby.network.msg_count."
    out["net_data_msgs"] = raw.get(f"{msg_pref}Data")
    out["net_request_ctrl_msgs"] = raw.get(f"{msg_pref}Request_Control")
    out["net_response_data_msgs"] = raw.get(f"{msg_pref}Response_Data")
    out["net_response_ctrl_msgs"] = raw.get(f"{msg_pref}Response_Control")

    # TCP (GPU L1D) — sum hits/misses across all CUs
    tcp_h, tcp_n = _sum_indexed(
        raw, "system.ruby.tcp_cntrl", ".L1cache.m_demand_hits")
    tcp_m, _ = _sum_indexed(
        raw, "system.ruby.tcp_cntrl", ".L1cache.m_demand_misses")
    out["tcp_count"] = tcp_n
    out["tcp_hits"] = tcp_h
    out["tcp_misses"] = tcp_m
    if tcp_h + tcp_m > 0:
        out["tcp_hit_rate"] = tcp_h / (tcp_h + tcp_m)

    # SQC (GPU L1I)
    sqc_h, sqc_n = _sum_indexed(
        raw, "system.ruby.sqc_cntrl", ".L1cache.m_demand_hits")
    sqc_m, _ = _sum_indexed(
        raw, "system.ruby.sqc_cntrl", ".L1cache.m_demand_misses")
    out["sqc_count"] = sqc_n
    if sqc_h + sqc_m > 0:
        out["sqc_hit_rate"] = sqc_h / (sqc_h + sqc_m)

    # TCC (GPU L2) — only present in VIPER, not Spandex-TU's flat-LLC variant
    # but ALSO present in our TU build (which keeps TCC). Either way, sum.
    tcc_h, tcc_n = _sum_indexed(
        raw, "system.ruby.tcc_cntrl", ".L2cache.m_demand_hits")
    tcc_m, _ = _sum_indexed(
        raw, "system.ruby.tcc_cntrl", ".L2cache.m_demand_misses")
    out["tcc_count"] = tcc_n
    if tcc_h + tcc_m > 0:
        out["tcc_hit_rate"] = tcc_h / (tcc_h + tcc_m)

    # Spandex Directory (LLC) — only present in Spandex-TU runs
    out["dir_request_msgs"] = raw.get(
        "system.ruby.dir_cntrl0.requestToDir.m_msg_count")
    out["dir_response_msgs"] = raw.get(
        "system.ruby.dir_cntrl0.responseFromDir.m_msg_count")
    out["dir_memory_msgs"] = raw.get(
        "system.ruby.dir_cntrl0.requestToMemory.m_msg_count")

    # GPU_VIPER baseline directory: same per-machine name structure but
    # different protocol semantics. The keys above already cover both.

    # CPU (TCP-as-CPU) on Spandex side
    cpu_h, cpu_n = _sum_indexed(
        raw, "system.ruby.cpu_cntrl", ".L1cache.m_demand_hits")
    cpu_m, _ = _sum_indexed(
        raw, "system.ruby.cpu_cntrl", ".L1cache.m_demand_misses")
    out["cpu_count"] = cpu_n
    if cpu_h + cpu_m > 0:
        out["cpu_hit_rate"] = cpu_h / (cpu_h + cpu_m)

    # Total network traffic — single number is useful for "X% reduction"
    # in the paper.
    net_total = sum(
        v for v in (out["net_data_msgs"], out["net_request_ctrl_msgs"],
                    out["net_response_data_msgs"],
                    out["net_response_ctrl_msgs"])
        if v is not None
    )
    out["net_total_msgs"] = net_total

    return out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

_PRINT_KEYS = [
    ("simSeconds",            "sim time (s)",          ".6f"),
    ("simTicks",              "sim ticks",             ".0f"),
    ("hostSeconds",           "host time (s)",         ".1f"),
    ("net_total_msgs",        "net msgs (total)",      ".0f"),
    ("net_data_msgs",         "  data",                ".0f"),
    ("net_request_ctrl_msgs", "  req_ctrl",            ".0f"),
    ("net_response_data_msgs","  resp_data",           ".0f"),
    ("net_response_ctrl_msgs","  resp_ctrl",           ".0f"),
    ("tcp_hit_rate",          "GPU L1D hit rate",      ".3f"),
    ("sqc_hit_rate",          "GPU L1I hit rate",      ".3f"),
    ("tcc_hit_rate",          "GPU L2  hit rate",      ".3f"),
    ("cpu_hit_rate",          "CPU L1  hit rate",      ".3f"),
    ("dir_request_msgs",      "dir requests",          ".0f"),
    ("dir_memory_msgs",       "dir→memory",            ".0f"),
]


def _fmt(v, spec):
    if v is None:
        return "—"
    try:
        return format(v, spec)
    except (TypeError, ValueError):
        return str(v)


def print_table(rows: list[dict[str, Any]]) -> None:
    """Side-by-side comparison table on stdout."""
    if not rows:
        print("No m5out directories supplied.", file=sys.stderr)
        return
    # Header
    label_w = max(len(label) for _, label, _ in _PRINT_KEYS)
    col_w = max(20, max(len(Path(r["m5out"]).name) for r in rows))
    print(f"{'metric':<{label_w}}", end="  ")
    for r in rows:
        print(f"{Path(r['m5out']).name:>{col_w}}", end="  ")
    print()
    print("-" * (label_w + (col_w + 2) * len(rows)))
    for key, label, spec in _PRINT_KEYS:
        print(f"{label:<{label_w}}", end="  ")
        for r in rows:
            print(f"{_fmt(r.get(key), spec):>{col_w}}", end="  ")
        print()


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    keys = sorted({k for r in rows for k in r.keys()})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    print(f"\nWrote {len(rows)} rows → {path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("m5out_dirs", nargs="+",
                   help="Paths to m5out/ directories with stats.txt")
    p.add_argument("--csv", type=Path, default=None,
                   help="Also write a CSV with all metrics")
    args = p.parse_args(argv)

    rows = [extract(Path(d)) for d in args.m5out_dirs]
    print_table(rows)
    if args.csv is not None:
        write_csv(args.csv, rows)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
