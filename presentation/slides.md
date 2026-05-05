# Spandex Coherence in gem5: A TU Bridge for VIPER GPU
## ECE 757 Final Presentation — 8 min + 2 min Q&A

> Speaker notes are in italics. Times are cumulative; aim to be a bit
> under at each checkpoint to absorb transitions.

---

## Slide 1 — Title (≈30 s)

**Spandex Coherence in gem5: A Translation-Unit Bridge for VIPER GPU**

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

## Slide 7 — Validation: random tester (≈1 min)

**`ruby_gpu_random_test.py`** at three sizes:

| System         | Episodes  | Final tick     | Result   |
|----------------|-----------|----------------|----------|
| small          | 1         | 4,162          | **PASS** |
| medium         | 16        | 77,511         | **PASS** |
| large / TL=100 | **3,200** | **52,166,435** | **PASS** |

**vs. simplified single-protocol Spandex** (no TCC, no TU):

| Variant              | Large/TL=100 ticks | vs simplified |
|----------------------|--------------------|---------------|
| Simplified Spandex   | 33,174,089         | 1.00×         |
| **Spandex TU (this)**| **52,166,435**     | **1.57×**     |

*The 57% overhead is the cost of routing every miss through the real
GPU L2 + the TU. That's the tax of keeping VIPER untouched. Whether
it's worth it depends on the integration cost saved.*

---

## Slide 8 — Pannotia results (≈1 min)

**[FILL IN — Pannotia BC + PageRank under apu\_se.py inside Docker]**

```
[Bar chart: ticks for BC and PageRank
 — Stock VIPER vs Spandex TU
 — input: 1k_128k.gr]
```

```
[Bar chart: network message count by class
 (Data, Request_Control, Response_Data, Response_Control)
 — Stock VIPER vs Spandex TU]
```

Key takeaway: *(fill after data lands)*

---

## Slide 9 — Lessons + Future Work (≈45 s)

**Lessons:**
- SLICC bring-up is mechanical: traceback → fix → rebuild.
- gem5's MachineType is a closed enum — new types require editing the
  master `RubySlicc_Exports.sm`.
- gem5.opt isn't portable across glibc/Python: builds for HIP runs
  must be done **inside** the gcn-gpu container.

**Future work:**
- CPU-side TU (MESI CorePair → Spandex). ~700 lines. Demonstrates the
  full heterogeneous-coherence story.
- Per-word LLC state (Spandex paper's headline; eliminates false
  sharing). ~2 weeks.
- Sharer tracking + Inv broadcast — needed to support `ReqS`/`ReqO`.
- L3 eviction with O-inclusivity (V_I transitions).

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

| Time | Slide | Beat                                    |
|------|-------|-----------------------------------------|
| 0:00 | 1     | Intro                                    |
| 0:30 | 2     | Why two protocols?                       |
| 1:30 | 3     | Spandex's 7-request menu                 |
| 2:30 | 4     | Our architecture diagram                 |
| 4:00 | 5     | Translation table                        |
| 5:00 | 6     | 14-fix bring-up reality                   |
| 6:00 | 7     | Random-tester pass + 57% TU overhead     |
| 7:00 | 8     | Pannotia comparison plots                 |
| 8:00 | 9     | Lessons + future work                    |
| 8:30 |       | Hand to Q&A                              |

If running long, **cut Slide 5 to one mapping table** and let Slide 4
carry the architecture story.
