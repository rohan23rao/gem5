#!/bin/bash
# run_benchmarks.sh — Run Pannotia BC + PageRank under both Spandex-TU and
# stock GPU_VIPER inside the gcn-gpu Docker container.
#
# Prerequisites:
#   - build/SpandexC/gem5.opt   (container-built Spandex; this work)
#   - build/VEGA_X86_C/gem5.opt (container-built VIPER baseline)
#   - /home/rohan/752project/gem5-resources/src/gpu/pannotia/{bc,pagerank}/bin/
#     contains the HIP binaries
#   - /home/rohan/752project/datasets/pannotia/1k_128k.gr is the test graph
#   - Docker Desktop is running
#
# Usage:
#   bash scripts/run_benchmarks.sh           # run all
#   bash scripts/run_benchmarks.sh bc        # bc only
#   bash scripts/run_benchmarks.sh pagerank  # pagerank only

set -e

GEM5_DIR="/home/rohan/ece757/gem5"
PROJECT_DIR="/home/rohan/752project"
GRAPH="/workspace/datasets/pannotia/1k_128k.gr"

# Per-benchmark run options. Keep input small so timings are tractable.
declare -A BENCH_BIN=(
    [bc]="bc"
    [pagerank]="pagerank"
)

declare -A BENCH_DIR=(
    [bc]="/workspace/gem5-resources/src/gpu/pannotia/bc/bin"
    [pagerank]="/workspace/gem5-resources/src/gpu/pannotia/pagerank/bin"
)

declare -A BENCH_OPTS=(
    [bc]="$GRAPH 0"          # graph + source vertex
    [pagerank]="$GRAPH 1"    # graph + 1 = SPMV variant; 0 = baseline
)

declare -A PROTOCOL_BIN=(
    [spandex]="/gem5/build/SpandexC/gem5.opt"
    [viper]="/gem5/build/VEGA_X86_C/gem5.opt"
)

run_one() {
    local bench=$1
    local protocol=$2
    local outdir="m5out_${protocol}_${bench}"

    if [[ ! -d "$outdir" ]]; then mkdir -p "$outdir"; fi

    local outdir_in_container="/gem5/${outdir}"

    echo "=== ${protocol^^} | ${bench} | output → $outdir ==="
    docker.exe run --platform linux/amd64 --rm \
        -v "$(wslpath -w $GEM5_DIR)":/gem5 \
        -v "$(wslpath -w $PROJECT_DIR)":/workspace \
        -w /workspace \
        ghcr.io/gem5/gcn-gpu:v24-0 \
        "${PROTOCOL_BIN[$protocol]}" \
            -d "$outdir_in_container" \
            /gem5/configs/example/apu_se.py \
            -n 3 \
            --mem-size=8GB \
            --benchmark-root="${BENCH_DIR[$bench]}" \
            -c "${BENCH_BIN[$bench]}" \
            --options="${BENCH_OPTS[$bench]}" \
        2>&1 | tee "${outdir}.log" | tail -8
    echo "=== ${protocol^^} | ${bench} | done ==="
}

WHICH=${1:-all}
PROTOCOLS="${2:-spandex viper}"

cd "$GEM5_DIR"

for proto in $PROTOCOLS; do
    if [[ "$WHICH" == "all" || "$WHICH" == "bc" ]]; then
        run_one bc $proto
    fi
    if [[ "$WHICH" == "all" || "$WHICH" == "pagerank" ]]; then
        run_one pagerank $proto
    fi
done

echo
echo "=== Stat summary ==="
python3 scripts/extract_stats.py \
    m5out_spandex_bc m5out_viper_bc \
    m5out_spandex_pagerank m5out_viper_pagerank \
    --csv results/comparison.csv 2>/dev/null || echo "  (some m5outs may be missing — re-run when builds land)"
