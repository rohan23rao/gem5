#!/usr/bin/env python3
"""
plot_comparison.py — Render side-by-side bar charts comparing two
coherence protocols (Spandex-TU vs GPU_VIPER) across one or more
benchmarks.

Usage:
    python3 scripts/plot_comparison.py results/comparison.csv results/plots/

The CSV is the output of `extract_stats.py --csv`. Each row is
(protocol, benchmark) keyed by the m5out directory name pattern
'm5out_<protocol>_<benchmark>'.

We parse the m5out column to derive (protocol, benchmark) pairs, then
emit one PNG/SVG per metric.
"""

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

# matplotlib uses a non-interactive backend so this works headless.
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


# ---------------------------------------------------------------------------
# Metric definitions — what to plot, with axis labels
# ---------------------------------------------------------------------------

METRICS = [
    ("simTicks",            "Simulation ticks",            "ticks"),
    ("simSeconds",          "Simulated time",              "seconds"),
    ("net_total_msgs",      "Total network messages",      "msgs"),
    ("net_data_msgs",       "Data messages",               "msgs"),
    ("net_request_ctrl_msgs","Request-control messages",   "msgs"),
    ("net_response_data_msgs","Response-data messages",    "msgs"),
    ("dir_request_msgs",    "Directory requests",          "msgs"),
    ("dir_memory_msgs",     "DRAM accesses",               "accesses"),
    ("tcp_hit_rate",        "GPU L1D hit rate",            "rate"),
    ("sqc_hit_rate",        "GPU L1I hit rate",            "rate"),
    ("tcc_hit_rate",        "GPU L2 hit rate",             "rate"),
    ("cpu_hit_rate",        "CPU L1 hit rate",             "rate"),
]

# Protocol → bar color
COLORS = {
    "spandex": "#1f77b4",   # blue
    "viper":   "#d62728",   # red (the baseline)
}

# Match m5out directory naming: m5out_<protocol>_<benchmark>
DIR_RE = re.compile(r"m5out_([a-z]+)_(.+)$")


# ---------------------------------------------------------------------------
# CSV → (protocol, benchmark) → metric dict
# ---------------------------------------------------------------------------

def parse_csv(csv_path: Path) -> dict:
    """Returns {benchmark: {protocol: {metric: value}}}."""
    out = defaultdict(lambda: defaultdict(dict))
    with csv_path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            m5out = row.get("m5out", "")
            name = Path(m5out).name
            m = DIR_RE.match(name)
            if not m:
                print(f"  skip: '{name}' doesn't match m5out_<protocol>_<bench>",
                      file=sys.stderr)
                continue
            protocol, bench = m.group(1), m.group(2)
            for k, v in row.items():
                if v in ("", None):
                    continue
                try:
                    out[bench][protocol][k] = float(v)
                except ValueError:
                    pass
    return out


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def plot_metric(data: dict, metric_key: str, title: str, ylabel: str,
                outdir: Path) -> None:
    """data: {benchmark: {protocol: {metric: value}}}"""
    benches = sorted(data.keys())
    protocols = sorted({p for b in data.values() for p in b.keys()})
    if not benches:
        return

    x = np.arange(len(benches))
    width = 0.8 / max(1, len(protocols))

    fig, ax = plt.subplots(figsize=(max(6, 1.5 * len(benches)), 4.5))

    for i, proto in enumerate(protocols):
        vals = [data[b].get(proto, {}).get(metric_key, np.nan)
                for b in benches]
        # Skip metric if no data
        if all(np.isnan(v) for v in vals):
            continue
        offset = (i - (len(protocols) - 1) / 2) * width
        bars = ax.bar(x + offset, vals, width,
                      label=proto.upper(),
                      color=COLORS.get(proto, "#999"),
                      edgecolor="black", linewidth=0.5)
        # Numeric labels above bars
        for bar, val in zip(bars, vals):
            if not np.isnan(val):
                ax.annotate(f"{val:,.3g}",
                            xy=(bar.get_x() + bar.get_width()/2, val),
                            xytext=(0, 3), textcoords="offset points",
                            ha="center", va="bottom", fontsize=8)

    ax.set_title(title)
    ax.set_xlabel("Benchmark")
    ax.set_ylabel(ylabel)
    ax.set_xticks(x)
    ax.set_xticklabels(benches, rotation=15, ha="right")
    ax.legend(loc="best")
    ax.grid(axis="y", alpha=0.3, linestyle="--")
    fig.tight_layout()

    outdir.mkdir(parents=True, exist_ok=True)
    base = outdir / f"compare_{metric_key}"
    fig.savefig(f"{base}.png", dpi=150)
    fig.savefig(f"{base}.svg")
    plt.close(fig)
    print(f"  ✓ {base}.png + .svg")


# ---------------------------------------------------------------------------
# Speedup helper — useful "% reduction in X" for the paper
# ---------------------------------------------------------------------------

def print_speedup_table(data: dict, baseline: str = "viper",
                        target: str = "spandex") -> None:
    print(f"\nRelative metrics ({target.upper()} vs {baseline.upper()} baseline):")
    print(f"{'benchmark':<20} {'metric':<25} {'baseline':>12} "
          f"{'spandex':>12} {'spandex/base':>14}")
    print("-" * 86)
    for bench in sorted(data.keys()):
        row = data[bench]
        if baseline not in row or target not in row:
            print(f"{bench:<20} (missing protocol data)")
            continue
        for key, label, _ in METRICS:
            b = row[baseline].get(key)
            t = row[target].get(key)
            if b is None or t is None or b == 0:
                continue
            print(f"{bench:<20} {label:<25} {b:>12,.3g} {t:>12,.3g} "
                  f"{t/b:>14.3f}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("csv", type=Path,
                   help="comparison.csv produced by extract_stats.py --csv")
    p.add_argument("outdir", type=Path,
                   help="directory to write plots into")
    p.add_argument("--baseline", default="viper",
                   help="protocol name used as 1.0× reference (default: viper)")
    p.add_argument("--target", default="spandex",
                   help="protocol name to compare against baseline (default: spandex)")
    args = p.parse_args(argv)

    data = parse_csv(args.csv)
    if not data:
        print("No data parsed; check CSV format.", file=sys.stderr)
        return 1

    print(f"Plotting {len(data)} benchmark(s) → {args.outdir}/")
    for key, title, ylabel in METRICS:
        plot_metric(data, key, title, ylabel, args.outdir)

    print_speedup_table(data, args.baseline, args.target)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
