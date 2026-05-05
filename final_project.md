# Spandex Coherence in gem5: A Translation-Unit Bridge for VIPER GPU

> ECE 757 (UW–Madison), Spring 2026.
> **Authors:** Rohan Rao, Tanya, Shao-Kai, Yash, Mohith.
> **Branch / artifact:** `dev` of `https://github.com/rohan23rao/gem5`.
> **Status:** Implementation + correctness validation complete; comparative
> evaluation in progress (data fills below as runs land).

---

## Abstract

Heterogeneous CPU–GPU systems require coherence protocols that accommodate
fundamentally different memory access patterns: CPUs favor ownership-based
MESI-style coherence for producer–consumer sharing, while GPUs benefit from
write-through with self-invalidation for SIMT execution. The current
mainstream GPU coherence protocol in gem5 — AMD's GPU\_VIPER — uses
separate coherence mechanisms for CPU and GPU with a shared LLC that must
translate between them, limiting flexibility and preventing per-access
adaptation.

We implement the Spandex flexible coherence protocol (Alsop et al.,
ISCA 2018) in gem5's Ruby memory system as an alternative LLC that all
device classes can target. Instead of replacing the GPU's native VIPER L1
hierarchy, we keep VIPER's TCP/SQC/TCC stack untouched and insert a new
**Translation Unit (TU)** SLICC controller between TCC's outbound NB
interface and a new Spandex directory. The TU translates VIPER's
`CPURequestMsg` vocabulary (RdBlk / WriteThrough / WriteFlush / Atomic\*)
into Spandex's request menu (`ReqV` / `ReqWTD` / `ReqOD` / `ReqWB`) and
synthesizes the matching `ResponseMsg` chain back to TCC, so VIPER
remains an unmodified third-party IP block.

We validate correctness with gem5's official GPU random-protocol tester
at three system sizes, then compare against an unmodified GPU\_VIPER
baseline on the same tester and on Pannotia BC + PageRank under
`apu_se.py`. Initial random-tester results show the Spandex-TU pipeline
adds approximately 57% more simulated ticks than the simplified
single-protocol Spandex on the same workload — the cost of routing every
miss through a real GPU L2 plus the TU.

---

## 1. Motivation

CPUs and GPUs make fundamentally incompatible coherence demands:

- **CPU producers/consumers** thrive under MESI/MOESI: a private dirty
  line, ownership transfers, sharer tracking, and explicit invalidations.
- **GPU SIMT** thrives under write-through self-invalidation: cheap
  stores (no invalidations on the network), self-invalidating L1 caches
  at fence boundaries, and atomic RMW at a shared point (typically the
  L2).

Forcing both onto a single protocol cripples whichever side is the loser.
gem5's GPU\_VIPER uses VIPER on the GPU side and a separate MOESI directory
for CPU side, with a hierarchical NB ("Northbridge") translation layer
in between. This works but bakes the translation into a fixed hierarchy.

Spandex's contribution is a **flexible LLC interface** with seven request
types (`ReqV`, `ReqS`, `ReqWT`, `ReqO`, `ReqWT+data`, `ReqO+data`,
`ReqWB`) such that each device class translates its native operations
into the request that best matches its semantics. The LLC arbitrates one
unified request stream. Translation lives in per-device TUs at the L2↔LLC
boundary; native L1 protocols stay untouched.

For our project we focus on the GPU side: building **one TU that bridges
unmodified VIPER GPU through to a Spandex LLC**. CPU side reuses the same
TCP-as-CPU pattern from the simplified-Spandex baseline. Two device
flavours reach the same LLC; the TU is the demonstration that translation
can be added without modifying device IP.

---

## 2. Background

### 2.1 Spandex (ISCA 2018)

Per Spandex Table II, each device's translation rules pick a request
type per operation. For GPU coherence:

| GPU operation       | Spandex request | Meaning                                  |
|---------------------|-----------------|------------------------------------------|
| Load                | `ReqV`          | Read a clean copy; LLC doesn't track it. |
| Store               | `ReqWT`         | Write-through with payload.              |
| Atomic RMW          | `ReqOD`         | Ownership + data; LLC performs RMW.      |
| Release fence       | `ReqWB`         | Owner relinquishes.                      |
| Acquire fence       | (L1 self-invalidates) | No network message.                |

CPU side (MESI/MOESI):

| CPU operation       | Spandex request                                   |
|---------------------|---------------------------------------------------|
| Read miss           | `ReqS` (tracked sharer)                           |
| Write miss          | `ReqO+data`                                       |
| Silent S→M upgrade  | `ReqO`                                            |
| Eviction (dirty)    | `ReqWB`                                           |

### 2.2 gem5 Ruby and SLICC

Ruby is gem5's detailed coherence simulator. Each cache controller is a
SLICC (Specification Language for Implementing Cache Coherence) state
machine: a `.sm` file declares states, events, message-buffer ports,
actions, and transitions. SLICC compiles to C++. Communication is via
**virtual networks (vnets)** that prevent protocol-level deadlock.

### 2.3 GPU\_VIPER's hierarchy in gem5

```
CU → coalescer → TCP (L1D, per CU)  ┐
              SQC (L1I, shared)     ├─ vnets 1/3 ─→ TCC (L2, atomic ALU)
              scalar coalescer      ┘
                                       │
                          vnets 0/2/4 ─┴─→ NB (Northbridge)
                                          [L3Cache + Directory in stock VIPER]
```

TCC is the GPU L2 — it has the atomic ALU, write-coalescing, and the
MOESI-style probe interface. In stock VIPER, TCC's outbound vnets 0/2/4
go to a `MachineType:Directory` instance. **Our project replaces that
destination with `MachineType:TU`** (single-line edits at 10 sites in
`GPU_VIPER-TCC.sm`); VIPER otherwise stays untouched.

---

## 3. Implementation

### 3.1 Architecture

```
        VIPER GPU (untouched)
        CU → TCP/SQC → TCC
                        │  vnets 0/2/4 (CPURequestMsg / ResponseMsg / Unblock)
                        ▼
               ┌──────────────────┐
               │  Spandex TU      │  ← NEW (Spandex_TU.sm)
               │  MachineType:TU  │
               └──────────────────┘
                        │  vnets 1/3 (Spandex{Request,Response}Msg)
                        ▼
               Spandex LLC + L3   (Spandex-dir.sm; reused from simplified Spandex)
                        │
                        ▼
                       DRAM
```

### 3.2 Files

**New:**
- `src/mem/ruby/protocol/Spandex_TU.sm` — TU controller (~700 lines).
  States `{I, W}`; events for each VIPER request type; per-address TBE
  carrying the original `CURequestor` so responses route back to TCP.

**Modified (small surgical edits):**
- `src/mem/ruby/protocol/GPU_VIPER-TCC.sm` — 10 sites changed from
  `MachineType:Directory` to `MachineType:TU`. **No state-machine
  semantics changed.**
- `src/mem/ruby/protocol/RubySlicc_Exports.sm` — added `TU` to the master
  `MachineType` enumeration (gem5's enum is closed; new types must be
  registered here).
- `src/mem/ruby/protocol/Spandex.slicc` — manifest now: `MOESI_AMD_Base-msg.sm`
  → `GPU_VIPER-msg.sm` → `Spandex-msg.sm` → `GPU_VIPER-TCP/SQC.sm` →
  `Spandex_TU.sm` → `GPU_VIPER-TCC.sm` → `Spandex-dir.sm` → `Spandex-DMA.sm`.
  Order matters: TU must be declared before TCC references `MachineType:TU`.
- `src/mem/ruby/protocol/Spandex-msg.sm` — removed redundant `VIPERCoalescer`
  external decl (already in `GPU_VIPER-msg.sm`).
- `configs/ruby/Spandex.py` — major rewrite to instantiate
  `Spandex_TCP_Controller` (built from VIPER's TCP source under our
  protocol prefix), `Spandex_TCC_Controller`, `Spandex_TU_Controller`,
  `Spandex_Directory_Controller`, plus all eight TCC MessageBuffers
  (including `triggerQueue`) and the missing TCP probe/unblock ports.

**Reused from simplified Spandex (`stable` branch):**
- `Spandex-dir.sm` — line-granular LLC (states `I/V/I_V/I_O`); request
  menu `ReqV/ReqWTD/ReqOD/ReqWB`; performs atomic RMW in-line.
- `Spandex-DMA.sm` — minimal DMA controller speaking Spandex natively.

### 3.3 TU translation table

| TCC outbound (CPURequestMsg) | TU emits (SpandexRequestMsg) | Notes                                  |
|------------------------------|------------------------------|----------------------------------------|
| `RdBlk`                      | `ReqV`                       | GPU read miss                          |
| `WriteThrough`               | `ReqWTD`                     | TCC's eviction of dirty line / WT      |
| `WriteFlush`                 | `ReqWB`                      | Release fence                          |
| `wb_writeBack`               | `ReqWB`                      | TCC eviction; no `CURequestor`         |
| `AtomicReturn`               | `ReqOD`                      | Atomic with return value               |
| `AtomicNoReturn`             | `ReqOD`                      | Atomic without return value            |

| Spandex inbound (SpandexResponseMsg) | TU emits to TCC (ResponseMsg) | TCC trigger    |
|--------------------------------------|--------------------------------|----------------|
| `Data`                               | `NBSysResp`, with DataBlk      | `TCC_Ack`      |
| `Ack`                                | `NBSysWBAck`                   | `TCC_AckWB`    |
| `WBAck`                              | `NBSysWBAck`                   | `TCC_AckWB`    |
| `AtomicResp`                         | `NBSysResp`, with DataBlk      | `TCC_Ack`      |
| `AtomicDone`                         | `NBSysWBAck`                   | `TCC_AckWB`    |

The TU saves the original `CURequestor` (the TCP that issued the miss)
in its TBE and injects it into the synthesized `ResponseMsg`, preserving
TCC's expected behaviour exactly.

`UnblockMsg` from TCC (vnet 4) is absorbed by the TU silently — the
Spandex Dir has no unblock mechanism. Probes from Spandex Dir back to
TCC are not generated in this implementation (GPU coherence has no
tracked sharers, so no Inv/RvkO traffic should arise).

### 3.4 Bring-up: 14 fixes to the TU

The TU started as an untested ~700-line draft. Getting it to compile and
pass the random tester required 14 distinct fixes:

| # | File | Fix |
|---|---|---|
| 1 | `Spandex-msg.sm` | Removed duplicate `VIPERCoalescer` external decl (already in GPU_VIPER-msg.sm). |
| 2 | `Spandex.slicc` | Added `MOESI_AMD_Base-msg.sm` (defines `CPURequestMsg` etc.). |
| 3 | `Spandex.slicc` | Reordered: `Spandex_TU.sm` before `GPU_VIPER-TCC.sm`. |
| 4 | `RubySlicc_Exports.sm` | Added `TU` to master `MachineType` enum. |
| 5 | `Spandex_TU.sm` | `functionalWrite` returned `bool`; signature is `int` — use accumulator pattern. |
| 6 | `Spandex_TU.sm` | Added missing `mapAddressToMachine()` prototype. |
| 7 | `Spandex.py` | `GPU_VIPER_*_Controller` → `Spandex_*_Controller` (SLICC names by active protocol). |
| 8 | `Spandex.py` | TCC doesn't take `TCC_select_num_bits` (that's a TCP arg). |
| 9 | `Spandex.py` | Added missing TCC knobs (`l2_request_latency`, `l2_response_latency`, `glc_atomic_latency`, `WB`, `number_of_TBEs`) + `triggerQueue` MessageBuffer. |
| 10 | `Spandex.py` | TCC's outbound buffer is `responseToCore`, not `responseToTCP`. |
| 11 | `Spandex.py` | Added missing TCP buffers `responseFromTCP`, `unblockFromCore`, `probeToTCP`. |
| 12 | `Spandex.py` | Added missing SQC buffer `probeToSQC`. |
| 13 | `Spandex.py` | Added missing scalar buffer `probeToSQC`. |
| 14 | `Spandex.py` | `number_of_virtual_networks` 5 → 6 (vnet 5 needed for TCP `unblockFromCore`). |

These are documented in the commit message of the bring-up commit.

---

## 4. Evaluation

### 4.1 Methodology

Each protocol is built with `scons --linker=gold` against gem5 v25.1.
Random tester runs `configs/example/ruby_gpu_random_test.py` with
`--num-dmas 0` and the indicated `--test-length` and `--system-size`.

| System size | CUs | WFs | DMAs |
|---|---|---|---|
| small  | 1 | 1 | 0 |
| medium | 4 | 16 | 0 |
| large  | 8 | 32 | 0 |

For HIP benchmarks, BC and PageRank from Pannotia are built inside the
`ghcr.io/gem5/gcn-gpu:v24-0` Docker image with the test graph
`1k_128k.gr` from `dist.gem5.org`. gem5 itself is run inside the same
container so HIP runtime libraries resolve.

### 4.2 Random tester correctness — Spandex TU

| System | Test length | Episodes completed | Final tick     | Result   |
|--------|-------------|--------------------|----------------|----------|
| small  | 1           | 1                  | 4,162          | **PASS** |
| medium | 1           | 16                 | 77,511         | **PASS** |
| large  | 100         | 3,200              | **52,166,435** | **PASS** |

The large/test-length-100 result corresponds to ~3,200 random episodes
across 32 wavefronts on 8 CUs. Passing this stress test means the TU's
state machine and the Spandex Dir together handle the random mix of
loads, stores, atomics, and fences without deadlock.

### 4.3 Comparison with simplified single-protocol Spandex

Reference numbers from the same random tester running against the
simplified-Spandex implementation on the `stable` branch — where the GPU
L1 emits Spandex requests directly (no TCC, no TU):

| System         | Stable Spandex (simplified) | Spandex TU (this work) | TU overhead |
|----------------|-----------------------------|------------------------|-------------|
| large / TL=100 | 33,174,089 ticks            | 52,166,435 ticks       | **+57.2%**  |

The TU pipeline adds two extra hops per L1 miss (TCP→TCC, TCC→TU→Dir)
plus TCC's atomic ALU and write-coalescing, which is the entire point of
keeping VIPER's hierarchy untouched. The overhead is the cost of IP
reuse.

### 4.4 Comparison with stock GPU\_VIPER baseline

*(Numbers fill in after the VIPER baseline build completes.)*

| System | Stock GPU\_VIPER | Spandex TU | Spandex/VIPER |
|---|---|---|---|
| small  | TBD | 4,162      | TBD |
| medium | TBD | 77,511     | TBD |
| large/TL=100 | TBD | 52,166,435 | TBD |

### 4.5 Pannotia HIP benchmarks (in progress)

*(Numbers fill in after Pannotia BC and PageRank runs land. Both Spandex
TU and stock GPU\_VIPER will be built inside the gcn-gpu Docker container
so they link against the container's libpython3.6 and ROCm 4.0.1.)*

| Benchmark | Input        | Stock VIPER (ticks) | Spandex TU (ticks) | Network msgs ratio |
|-----------|--------------|----------------------|---------------------|-----|
| BC        | 1k\_128k.gr  | TBD                  | TBD                 | TBD |
| PageRank  | 1k\_128k.gr  | TBD                  | TBD                 | TBD |

---

## 5. Lessons learned + future work

**Lessons:**
- *SLICC bring-up is unforgiving but tractable.* Of the 14 bring-up bugs,
  most were syntactically obvious in retrospect (port name typos, missing
  prototypes, wrong protocol prefix on generated classes). The pattern
  for debugging is: read the Python traceback to find the SLICC error
  message, find the source line, fix, rebuild incrementally.
- *MachineType is a closed enum.* Adding a new machine type (TU) required
  editing `RubySlicc_Exports.sm` — gem5 doesn't expose this as a standard
  extension point. This caught us by surprise mid-build.
- *Two binaries, two ABIs.* The host-built gem5.opt links against
  libpython3.10; the gcn-gpu container has libpython3.6. Real benchmarks
  must be run inside the container with a container-built gem5 binary.
  Not transferable across systems.

**Future work:**
- *CPU-side TU.* Today the CPU L1 is the GPU TCP machine in
  `use_seq_not_coal=True` mode — a Spandex-native device, not MESI. A
  proper CPU TU would let MESI CorePair share the same Spandex LLC,
  fully demonstrating heterogeneous coherence. ~700 lines of new SLICC.
- *Per-word LLC state.* The Spandex paper's headline contribution is
  per-word ownership tracking at the LLC. Our LLC is line-granular —
  sufficient for a TU demo, but doesn't exercise the false-sharing
  benefit. Adding per-word would be a Phase 5 polish (~2 weeks).
- *Sharer tracking + Inv broadcast.* Required to support `ReqS`/`ReqO`
  from a real MESI client. We elided these because GPU coherence doesn't
  use them.
- *L3 eviction with O-inclusivity.* Our `Spandex-dir.sm` declares `V_I`
  but has no transitions out of it. Production-correct Spandex must
  send `RvkO` to current owner before dropping an owned line.

---

## 6. Per-author contributions

| Section                                       | Lead              |
|-----------------------------------------------|-------------------|
| Simplified Spandex LLC + DMA (`stable` branch)| Rohan             |
| TU initial implementation (`dev` branch)      | Tanya             |
| TU bring-up (compile fixes + bench validation)| Rohan             |
| Integration with apu\_se.py and Docker        | Shao-Kai, Yash    |
| Pannotia BC/PageRank build + evaluation       | Mohith            |
| Paper + presentation                          | Group             |

---

## 7. Reproducibility

All on branch `dev` of `https://github.com/rohan23rao/gem5`:

```bash
# Spandex TU build (host)
scons build/Spandex/gem5.opt -j$(nproc) --linker=gold

# Random tester regression
build/Spandex/gem5.opt configs/example/ruby_gpu_random_test.py \
    --num-dmas 0 --test-length 100 --system-size large
# Expected: GPU Ruby Tester: Passed!  Exit tick: 52,166,435

# GPU_VIPER baseline build (host) — requires reverting GPU_VIPER-TCC.sm
git checkout origin/stable -- src/mem/ruby/protocol/GPU_VIPER-TCC.sm
scons build/VEGA_X86/gem5.opt -j$(nproc) --linker=gold
git checkout HEAD -- src/mem/ruby/protocol/GPU_VIPER-TCC.sm   # restore TU edits

# Pannotia BC build (inside Docker)
docker run --platform linux/amd64 --rm \
    -v "$(wslpath -w /home/rohan/752project/gem5-resources)":/work \
    -w /work/src/gpu/pannotia/bc ghcr.io/gem5/gcn-gpu:v24-0 \
    bash -c "/opt/rocm/bin/hipcc -O1 --offload-arch=gfx902 \
        ../graph_parser/util.cpp BC.cpp -o bin/bc"

# Pannotia BC run (inside Docker, container-built gem5)
docker run --platform linux/amd64 --rm \
    -v "$(wslpath -w /home/rohan/ece757/gem5)":/gem5 \
    -v "$(wslpath -w /home/rohan/752project)":/workspace \
    -w /workspace ghcr.io/gem5/gcn-gpu:v24-0 \
    /gem5/build/Spandex/gem5.opt /gem5/configs/example/apu_se.py -n 3 \
        --benchmark-root=/workspace/gem5-resources/src/gpu/pannotia/bc/bin \
        -c bc --options="/workspace/datasets/pannotia/1k_128k.gr"
```

---

## References

[1] Alsop, J., Sinclair, M. D., & Adve, S. V. (2018). *Spandex: A Flexible
Interface for Efficient Heterogeneous Coherence.* ISCA '18, pp. 261–274.

[2] AMD GPU\_VIPER protocol in gem5: `src/mem/ruby/protocol/GPU_VIPER-*.sm`.

[3] gem5 Ruby + SLICC documentation:
`https://www.gem5.org/documentation/general_docs/ruby/`.

[4] Pannotia benchmarks: `https://github.com/pannotia/pannotia` (HIP port at
`gem5-resources/src/gpu/pannotia/`).
