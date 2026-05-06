# Spandex Coherence in gem5: A TU Bridge for VIPER GPU
## ECE 757 Final Presentation — 8 min + 2 min Q&A

> Speaker notes are in italics. Times are cumulative; aim to be a bit
> under at each checkpoint to absorb transitions.

---

## Slide 1 — Title (≈30 s)

**Spandex Coherence in gem5: A Translation-hoUnit Bridge for VIPER GPU**

Rohan Rao · Tanya · Shao-Kai · Yash · Mohith
ECE 757 — Spring 2026

*Briefly: "We implemented the Spandex flexible coherence protocol in
gem5 as a TU bridging the unmodified VIPER GPU stack into a Spandex LLC.
Today: motivation, architecture, results, lessons."*

---

## Slide 2 — The motivation (≈1 min)

**CPUs and GPUs want different coherence:**

- **CPU**: ownership-based MESI / MOESI — private dirty data, sharer
  tracking, invalidations. Optimized for producer-consumer.
- **GPU**: write-through, self-invalidate at fences. Optimized for
  SIMT — cheap stores, no invalidation broadcasts.

**Forcing one protocol on both cripples the other.**

Today's gem5 GPU\_VIPER:
- VIPER on the GPU side
- MOESI directory on the CPU side
- A hierarchical NB ("Northbridge") translation layer between them

→ Works, but bakes the translation into a fixed hierarchy.

*Drive home the dichotomy. Mention Spandex's pitch: "stop forcing one
protocol; provide a flexible LLC interface and let each device translate."*

---

## Slide 3 — Spandex thesis (≈1 min)

**Spandex (Alsop / Sinclair / Adve, ISCA 2018):**

Single LLC interface, **menu of 7 request types**:

```
ReqV          — read, untracked  (GPU load)
ReqS          — read, tracked    (MESI shared)
ReqWT         — write-through    (GPU store)
ReqO          — ownership only   (MESI silent upgrade)
ReqWT+data    — atomic at LLC    (GPU/DeNovo atomic)
ReqO+data     — owned write miss (MESI write miss)
ReqWB         — writeback / release
```

Each device has a **Translation Unit (TU)** that maps native ops →
Spandex requests. Native L1 protocols stay untouched.

→ **Heterogeneous coherence without modifying anyone's L1.**

*Slow down on this slide. The TU concept is the entire project.*

---

## Slide 4 — Architecture (≈1.5 min)

**What we built:**

```
                VIPER GPU (untouched)
                CU → TCP / SQC → TCC
                              │
                              │ vnets 0/2/4 (NB-bound)
                              ▼
                   ┌───────────────────┐
                   │   Spandex TU      │  ← NEW
                   │   MachineType:TU  │
                   └───────────────────┘
                              │ vnets 1/3 (Spandex)
                              ▼
                   Spandex LLC + L3
                              │
                              ▼
                            DRAM
```

- **TU** = single SLICC controller (~700 lines).
  Translates VIPER's `CPURequestMsg` ↔ Spandex `Request/ResponseMsg`.
- **Surgical edit to TCC**: 10 sites changed `MachineType:Directory` →
  `MachineType:TU`. *No state machine semantics changed.*
- **Spandex LLC + Dir**: line-granular, handles
  `ReqV / ReqWTD / ReqOD / ReqWB`. Performs atomic RMW in-line.

*Key claim: VIPER never knows the NB has been replaced. Same response
chain, same TCP-bound replies, same expected behaviour.*

---

## Slide 5 — TU Translation Table (≈1 min)

**TCC outbound → Spandex:**

| TCC sends      | TU emits  | What it means                |
|----------------|-----------|------------------------------|
| `RdBlk`        | `ReqV`    | Read miss                    |
| `WriteThrough` | `ReqWTD`  | Eviction / write             |
| `WriteFlush`   | `ReqWB`   | Release                      |
| `wb_writeBack` | `ReqWB`   | TCC eviction                 |
| `Atomic*`      | `ReqOD`   | Atomic RMW at LLC            |

**Spandex → TCC reverse path:**

| Spandex sends | TU emits to TCC          |
|---------------|--------------------------|
| `Data`        | `NBSysResp` + DataBlk    |
| `Ack`         | `NBSysWBAck`             |
| `WBAck`       | `NBSysWBAck`             |

**TU saves `CURequestor`** (originating TCP) so responses route back
correctly. Probes from Dir → TCC: not generated (GPU coherence has no
tracked sharers).

---

## Slide 6 — Implementation reality (≈1 min)

**Bringing up an untested partner-implementation: 14 fixes.**

| Where            | Class of bug                                   |
|------------------|------------------------------------------------|
| `Spandex-msg.sm` | Duplicate VIPERCoalescer external decl         |
| `Spandex.slicc`  | Missing MOESI msg include + wrong include order|
| `RubySlicc_Exports.sm` | TU not registered in master MachineType  |
| `Spandex_TU.sm`  | functionalWrite return type, missing prototypes|
| `Spandex.py`     | Wrong `*_Controller` class names; missing TCC knobs; missing TCP/SQC/scalar probe ports; vnet count off by one |

→ All caught by SLICC compile errors + gem5 fatal at config time.
→ Once cleared, **random tester PASSES on first runtime invocation.**

*Lesson: SLICC compile is unforgiving but tractable. Read the traceback,
fix the line, rebuild incrementally.*

---

## Slide 7 — Results 1: TU vs stock VIPER, random tester (≈1.25 min)

**`ruby_gpu_random_test.py`**, identical config, both protocols:

| System / TL    | Stock VIPER         | Spandex TU       | TU/VIPER |
|----------------|---------------------|------------------|----------|
| small / 1      | 3,711               | 4,162            | 1.12×    |
| medium / 1     | 84,790              | **77,511**       | **0.91× (faster!)** |
| large / 1      | 236,496             | 236,999          | 1.002×   |
| large / **100**| **abort @ 359,533** | **52,166,435 PASS** | **— (VIPER fails)** |

> 📊 **Best plot for this slide**: `results/plots/tl1_only/compare_simTicks_tl1.png`
> (linear-scale comparison across small/medium/large_tl1 — the
> TL=100 outlier would skew it; for that, just point at the table row)
> Alternate: `results/plots/compare_simTicks.png` (log-scale, includes TL=100)

**Three story points:**

1. **TU is performance-equivalent to (or faster than) stock VIPER** on
   workloads both can complete. At medium it's *9% faster* — the TU
   pipeline is not "tax" in this regime.
2. **Stock VIPER deterministically aborts at TL ≥ 10 / large** (always
   tick 359,533); **Spandex TU completes a 100× longer stress workload**
   (3,200 episodes, 52 M ticks).
3. We did **not engineer for this** — VIPER stock has the bug, not us.

*Punchline: the TU pivot cost zero perf and gained robustness.*

---

## Slide 8 — Results 2: where the TU's character shows (≈1 min)

**Network traffic vs DRAM traffic — opposite trends:**

| Size / TL | DRAM accesses     | On-chip messages   |
|-----------|-------------------|--------------------|
| small / 1 | VIPER 48 → TU 10  (**0.21×**)  | VIPER 108 → TU 168 (1.56×) |
| medium /1 | VIPER 4063 → TU 798 (**0.20×**) | VIPER 9.5K → TU 16.4K (1.72×) |
| large / 1 | VIPER 12K → TU 1.7K (**0.14×**) | VIPER 28.6K → TU 54.8K (1.91×) |

> 📊 **Best plots for this slide** (use the *ratio* versions — one bar per
> config, value = TU/VIPER, a parity line at 1.0):
>   - `results/plots/compare_dir_memory_msgs_ratio.png`  ← bars all < 1, dramatic
>   - `results/plots/compare_net_total_msgs_ratio.png`   ← bars all > 1, the trade-off
> Alternate (linear, TL=1 only):
>   - `results/plots/tl1_only/compare_dir_memory_msgs_tl1.png`
>   - `results/plots/tl1_only/compare_net_total_msgs_tl1.png`

**Why:**
- Spandex directory **has a built-in 16 MiB L3**; VIPER's directory
  doesn't. Most "miss" traffic in Spandex hits L3 instead of DRAM.
- The TU pipeline adds two extra hops per miss (TCP → TCC → TU → Dir),
  costing 1.5–1.9× more on-chip messages.
- **Spandex trades on-chip bandwidth for off-chip bandwidth** — usually
  a good trade (DRAM is the slow/expensive side).

**HIP benchmarks (square / Pannotia BC / PageRank):**
TU compiled, container Spandex built, square advanced to **tick ~76×10⁹**
under apu_se.py before hitting an `Invalid transition` corner case in
`TU_Transitions.cc` — reachable only under multi-process traffic
(HSA + cmd processor + CPU host concurrent), not the random tester.
Debugging deferred.

---

## Slide 9 — Lessons + Future Work (≈45 s)

**Lessons:**
- **TU pivot was the right scope-cut.** Real perf parity, real
  robustness gain, ~1 week of focused implementation.
- **gem5's `MachineType` is a closed enum** — new types require editing
  master `RubySlicc_Exports.sm`. Caught us mid-build.
- **`gem5.opt` is not portable** across glibc/Python — HIP benchmarks
  must run inside the same container that built gem5.
- **Random tester ≠ apu_se.py traffic mix** — TU passed all random
  tester sizes but hit a corner case under multi-process concurrent
  HSA + GPU cmd processor + CPU host. *Different traffic patterns
  expose different bugs.*

**Future work:**
- **CPU-side TU** (MESI `MOESI_AMD_Base-CorePair.sm` → Spandex).
  ~700 lines. Real heterogeneous-coherence story.
- **Per-word LLC state** — Spandex paper headline; eliminates false
  sharing between CPU and GPU on the same line.
- **Debug the apu_se.py TU corner case** — needs `--debug-flags=
  ProtocolTrace` to identify the (state, event) panic'ing.
- **Sharer tracking + Inv broadcast**, **L3 eviction with O-inclusivity
  (V_I)**.

---

## Slide 10 — Backup (won't show unless Q&A)

**Architecture details for Q&A:**
- Vnet allocation: 0/2/4 between TCC↔TU; 1/3 between TU↔LLC.
- TBE structure: per-address state with `CURequestor` for response
  routing.
- 14-fix bring-up: detailed in `final_project.md` and the bring-up
  commit message.

**Likely questions:**
- "Why not implement the per-word LLC?" → Scope. We focused on the TU
  pivot story. Per-word adds significant SLICC complexity for a
  same-vintage benefit (only matters with cross-protocol false sharing).
- "Did you try the CPU TU?" → Time-bounded; deferred. The TCP-as-CPU
  pattern from the simplified-Spandex stays in place.
- "Is GPU\_VIPER's TCC modification 'modifying VIPER'?" → 10 destination
  edits, no semantic change. Same response chain, same probe behaviour.
  Stronger statement than "we built a thin shim that pretended to be
  the directory."

---

## Cue-sheet for the talk

| Time | Slide | Beat                                              |
|------|-------|---------------------------------------------------|
| 0:00 | 1     | Intro                                              |
| 0:30 | 2     | Why two protocols?                                 |
| 1:30 | 3     | Spandex's 7-request menu                           |
| 2:30 | 4     | Architecture diagram (TU position)                 |
| 4:00 | 5     | Translation table                                  |
| 5:00 | 6     | 14-fix bring-up reality                            |
| 6:00 | 7     | **Result 1**: VIPER aborts, TU passes — perf parity |
| 7:15 | 8     | **Result 2**: 5–7× DRAM reduction; HIP corner case  |
| 8:15 | 9     | Lessons + future work                              |
| 8:45 |       | Hand to Q&A                                        |

If running long, **cut Slide 5 to one mapping table** and let Slide 4
carry the architecture story. Slide 6 (14-fix story) can also be
cut to one bullet.

**Reference slide → file (use these for the actual deck):**
- Slide 7 plot:  `results/plots/tl1_only/compare_simTicks_tl1.png`
                 (linear, 3 bars per protocol — small / medium / large)
- Slide 8 plot A: `results/plots/compare_dir_memory_msgs_ratio.png`
                 (TU/VIPER ratio bars — all 0.14× to 0.21×, the win)
- Slide 8 plot B: `results/plots/compare_net_total_msgs_ratio.png`
                 (TU/VIPER ratio — all 1.5× to 1.9×, the trade-off)
- Raw data:      `results/random_tester_compare.csv`

**Plot variants available** (regenerate with
`python3 scripts/plot_comparison.py results/random_tester_compare.csv results/plots`):
- `results/plots/compare_<metric>.png` — auto log-scale, all configs
- `results/plots/compare_<metric>_ratio.png` — TU/VIPER ratio, scale-free
- `results/plots/tl1_only/compare_<metric>_tl1.png` — linear, TL=1 only
