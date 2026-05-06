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
                outdir: Path, log_scale: bool = False,
                file_suffix: str = "") -> None:
    """data: {benchmark: {protocol: {metric: value}}}

    log_scale=True forces a log-y axis. Otherwise auto: log if the data
    spans more than 100× between min and max non-zero values.
    """
    benches = sorted(data.keys())
    protocols = sorted({p for b in data.values() for p in b.keys()})
    if not benches:
        return

    # Auto-detect log scale: if non-zero value range > 100×, use log.
    all_vals = [v for b in data.values() for proto in b.values()
                for k, v in proto.items() if k == metric_key
                and v is not None and not np.isnan(v) and v > 0]
    if not log_scale and all_vals:
        if max(all_vals) / min(all_vals) > 100:
            log_scale = True

    x = np.arange(len(benches))
    width = 0.8 / max(1, len(protocols))

    fig, ax = plt.subplots(figsize=(max(6, 1.5 * len(benches)), 4.5))

    for i, proto in enumerate(protocols):
        vals = [data[b].get(proto, {}).get(metric_key, np.nan)
                for b in benches]
        if all(np.isnan(v) for v in vals):
            continue
        offset = (i - (len(protocols) - 1) / 2) * width
        # For log scale, replace 0 with a tiny number so bars show
        plot_vals = vals
        if log_scale:
            plot_vals = [v if (v is not None and not np.isnan(v) and v > 0)
                         else float("nan") for v in vals]
        bars = ax.bar(x + offset, plot_vals, width,
                      label=proto.upper(),
                      color=COLORS.get(proto, "#999"),
                      edgecolor="black", linewidth=0.5)
        for bar, val in zip(bars, vals):
            if val is not None and not np.isnan(val):
                # Format compact for big numbers
                if abs(val) >= 1e6:
                    label = f"{val/1e6:.2f}M"
                elif abs(val) >= 1e3:
                    label = f"{val/1e3:.1f}K"
                else:
                    label = f"{val:,.3g}"
                ax.annotate(label,
                            xy=(bar.get_x() + bar.get_width()/2, val),
                            xytext=(0, 3), textcoords="offset points",
                            ha="center", va="bottom", fontsize=8)

    if log_scale:
        ax.set_yscale("log")
        title = f"{title} (log y)"

    ax.set_title(title)
    ax.set_xlabel("Benchmark / config")
    ax.set_ylabel(ylabel + (" — log" if log_scale else ""))
    ax.set_xticks(x)
    ax.set_xticklabels(benches, rotation=15, ha="right")
    ax.legend(loc="best")
    ax.grid(axis="y", alpha=0.3, linestyle="--",
            which="both" if log_scale else "major")
    fig.tight_layout()

    outdir.mkdir(parents=True, exist_ok=True)
    base = outdir / f"compare_{metric_key}{file_suffix}"
    fig.savefig(f"{base}.png", dpi=150)
    fig.savefig(f"{base}.svg")
    plt.close(fig)
    print(f"  ✓ {base}.png + .svg")


def plot_ratio(data: dict, metric_key: str, title: str, outdir: Path,
               baseline: str = "viper", target: str = "spandex",
               file_suffix: str = "_ratio") -> None:
    """One bar per benchmark, value = target/baseline ratio. 1.0 baseline
    line drawn for reference. Visualises 'TU/VIPER' independent of scale."""
    benches = sorted(data.keys())
    if not benches:
        return
    ratios = []
    labels = []
    for b in benches:
        if baseline in data[b] and target in data[b]:
            bv = data[b][baseline].get(metric_key)
            tv = data[b][target].get(metric_key)
            if bv is None or tv is None or bv == 0:
                continue
            ratios.append(tv / bv)
            labels.append(b)
    if not ratios:
        return

    x = np.arange(len(labels))
    fig, ax = plt.subplots(figsize=(max(5, 1.2 * len(labels)), 4.0))
    bars = ax.bar(x, ratios, 0.6,
                  color=[COLORS["spandex"] if r >= 1 else "#2ca02c" for r in ratios],
                  edgecolor="black", linewidth=0.5)
    ax.axhline(1.0, color="black", linewidth=0.8, linestyle="--",
               alpha=0.7, label="parity (VIPER baseline)")
    for bar, r in zip(bars, ratios):
        ax.annotate(f"{r:.3f}×",
                    xy=(bar.get_x() + bar.get_width()/2, r),
                    xytext=(0, 3), textcoords="offset points",
                    ha="center", va="bottom", fontsize=9)
    ax.set_title(f"{title} — Spandex TU / VIPER ratio")
    ax.set_xlabel("Config")
    ax.set_ylabel(f"ratio (1.0 = parity)")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=15, ha="right")
    ax.legend(loc="best")
    ax.grid(axis="y", alpha=0.3, linestyle="--")
    fig.tight_layout()
    outdir.mkdir(parents=True, exist_ok=True)
    base = outdir / f"compare_{metric_key}{file_suffix}"
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
        # 1) Auto-scale comprehensive plot (auto picks log if range > 100×)
        plot_metric(data, key, title, ylabel, args.outdir)
        # 2) Ratio plot (TU/VIPER), independent of scale
        plot_ratio(data, key, title, args.outdir,
                   baseline=args.baseline, target=args.target)

    # 3) TL=1-only subset where everything is comparable on linear scale
    tl1_data = {b: v for b, v in data.items() if "large" not in b or "tl1" in b}
    # the 'small' and 'medium' rows are TL=1; 'large_tl1' is TL=1; drop 'large' (TL=100)
    if tl1_data and tl1_data != data:
        print(f"\nLinear-scale TL=1-only subset ({len(tl1_data)} configs):")
        tl1_outdir = args.outdir / "tl1_only"
        for key, title, ylabel in METRICS:
            plot_metric(tl1_data, key, f"{title} (TL=1)", ylabel,
                        tl1_outdir, log_scale=False, file_suffix="_tl1")

    print_speedup_table(data, args.baseline, args.target)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
