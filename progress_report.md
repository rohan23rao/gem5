# ECE 757 Project Progress Report

## 1) Abstract

Heterogeneous CPU-GPU systems require coherence protocols that accommodate fundamentally different memory access patterns: CPUs favor ownership-based MESI-style coherence for producer-consumer sharing, while GPUs benefit from write-through with self-invalidation for SIMT execution. Current solutions like AMD's GPU_VIPER protocol in gem5 use separate coherence mechanisms for CPU and GPU with a shared LLC that must translate between them, limiting flexibility and preventing per-access adaptation.

Spandex addresses this by defining a unified LLC interface with a single set of request types — ReqV (valid copy), ReqWT+D (write-through with data), ReqO+D (ownership with data), and ReqWB (writeback) — that can express both GPU-style and CPU-style coherence. Each device-side translation unit maps its native operations to these common requests, enabling the LLC to handle heterogeneous traffic through one coherent interface.

We are implementing the Spandex protocol in gem5's Ruby memory system as an alternative to GPU_VIPER. Our implementation includes a new SLICC-based directory controller with L3 caching, a modified GPU L1 (TCP) controller that maps loads to ReqV, stores to ReqWT+D, and atomics to ReqO+D, and a GPU instruction cache (SQC) issuing ReqV for fetches. Key simplifications for this project include line-granularity state tracking at the LLC (deferring Spandex's per-word state innovation) and bypassing the GPU L2 (TCC) so the L1 communicates directly with the directory.

We evaluate correctness using gem5's GPU random protocol tester and compare performance against GPU_VIPER on Pannotia graph benchmarks, measuring execution time, network traffic, and cache miss rates.

## 2) What we've done so far

- Studied the Spandex protocol specification (Alsop et al., ISCA 2018) and gem5's Ruby memory system architecture, including all GPU_VIPER SLICC controllers and the MOESI_AMD_Base directory
- Designed the Spandex protocol mapping for GPU coherence: Load→ReqV, Store→ReqWT+D, Atomic→ReqO+D, Release→ReqWB, Acquire→self-invalidate
- Created `Spandex-msg.sm`: defined SpandexRequestType and SpandexResponseType enumerations, SpandexRequestMsg and SpandexResponseMsg message structures, and VIPERCoalescer external class declaration for SLICC
- Created `Spandex-TCP.sm`: full GPU L1 data cache controller with 3 states (Invalid, Valid, Atomic-waiting), 10 events, and complete transition table covering loads, write-through stores, atomics, flush/evict, and cache replacement
- Created `Spandex-SQC.sm`: GPU L1 instruction cache controller with 2 states, 4 events, and transitions for instruction fetch, data response, eviction, and replacement
- Created `Spandex-dir.sm`: LLC/directory controller with 5 states (I, V, I_V, I_O, V_I), 6 events, L3 cache + DirectoryMemory backing, and transitions for read hits/misses, write-throughs, atomics, writebacks, and memory responses
- Created `Spandex.slicc` protocol manifest listing all SLICC files in compilation order
- Created `SpandexCoalescer.cc/hh/py`: C++ coalescer inheriting from VIPERCoalescer, registered as a gem5 SimObject
- Modified `Kconfig` to register Spandex as a selectable Ruby protocol
- Modified `src/mem/ruby/system/SConscript` to include SpandexCoalescer in the build
- Configured the build system (`build/Spandex/`) and successfully compiled `gem5.opt` with zero SLICC or C++ errors

## 3) What we plan to do next

- Implement `configs/ruby/Spandex.py` configuration script by forking GPU_VIPER.py: instantiate TCP, SQC, and Directory controllers, remove TCC, wire message buffers on virtual networks 1 and 3, and set up the crossbar topology
- Add CPU and DMA controller support by including MOESI_AMD_Base files in the Spandex.slicc manifest and extending the directory to handle CPU-side messages alongside Spandex messages
- Run the GPU random protocol tester to validate correctness — identify and fix deadlocks, missing transitions, and data corruption bugs
- Run the `square` GPU benchmark as an end-to-end sanity check in SE mode
- Build the baseline GPU_VIPER binary and run identical benchmarks for comparison
- Run Pannotia graph benchmarks (BC, PageRank) under both protocols and collect stats: simulated execution time, network message counts/bytes, L1 miss rates, directory hit rates, and miss latency distributions
- Analyze results: explain why Spandex performs similarly to VIPER for GPU-only workloads (expected — both use write-through + self-invalidation) and discuss where Spandex's per-word state and unified interface would differentiate under mixed CPU-GPU workloads
