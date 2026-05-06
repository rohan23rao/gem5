# Spandex Coherence Protocol Configuration — Architecture B
#
# TCP → TCC → TU → Spandex Directory
#
# Key differences from Architecture A (teammate's version):
# - GPU_VIPER TCC (L2 cache) is kept between TCP and the Spandex LLC
# - A Translation Unit (TU) replaces the MOESI Northbridge interface
#   that TCC used to talk to.  The TU translates:
#     CPURequestMsg  (GPU_VIPER vocabulary) → SpandexRequestMsg
#     SpandexResponseMsg                    → ResponseMsg (GPU_VIPER vocabulary)
# - vnets 0/2/4 (TCC ↔ TU) and vnets 1/3 (TU ↔ Spandex Dir) are both needed,
#   so number_of_virtual_networks = 5

import math

from common import (
    FileSystemConfig,
    MemConfig,
    ObjectList,
)

import m5
from m5.defines import buildEnv
from m5.objects import *
from m5.util import addToPath

from .Ruby import (
    create_topology,
    send_evicts,
)

addToPath("../")

from topologies.Cluster import Cluster
from topologies.Crossbar import Crossbar


class CntrlBase:
    _seqs = 0

    @classmethod
    def seqCount(cls):
        CntrlBase._seqs += 1
        return CntrlBase._seqs - 1

    _cntrls = 0

    @classmethod
    def cntrlCount(cls):
        CntrlBase._cntrls += 1
        return CntrlBase._cntrls - 1

    _version = 0

    @classmethod
    def versionCount(cls):
        cls._version += 1
        return cls._version - 1


# ── L1 cache config (shared by TCP and CPU controllers) ───────────────────────

class TCPCache(RubyCache):
    size = "16KiB"
    assoc = 16
    dataArrayBanks = 16
    tagArrayBanks = 16
    dataAccessLatency = 4
    tagAccessLatency = 1

    def create(self, options):
        self.size = MemorySize(options.tcp_size)
        self.assoc = options.tcp_assoc
        self.resourceStalls = options.no_tcc_resource_stalls
        if hasattr(options, "tcp_rp"):
            self.replacement_policy = ObjectList.rp_list.get(options.tcp_rp)()


# ── TCC L2 cache config ───────────────────────────────────────────────────────

class TCCCache(RubyCache):
    size = "256KiB"
    assoc = 16
    dataArrayBanks = 16
    tagArrayBanks = 16
    dataAccessLatency = 8
    tagAccessLatency = 2

    def create(self, options):
        self.size = MemorySize(options.tcc_size)
        self.assoc = options.tcc_assoc
        self.resourceStalls = options.no_tcc_resource_stalls
        if hasattr(options, "tcc_rp"):
            self.replacement_policy = ObjectList.rp_list.get(options.tcc_rp)()


# ── Controller classes ────────────────────────────────────────────────────────

class TCPCntrl(Spandex_TCP_Controller, CntrlBase):
    """
    GPU L1 data cache — uses the unmodified GPU_VIPER TCP controller.
    Sends CPURequestMsg to TCC on vnet 1, receives ResponseMsg on vnet 3.
    TCC_select_num_bits selects which TCC (not Dir) to route to.
    """
    def create(self, options, ruby_system, system):
        self.version = self.versionCount()

        self.L1cache = TCPCache(
            tagAccessLatency=options.TCP_latency,
            dataAccessLatency=options.TCP_latency,
        )
        self.L1cache.resourceStalls = options.no_resource_stalls
        self.L1cache.dataArrayBanks = options.tcp_num_banks
        self.L1cache.tagArrayBanks = options.tcp_num_banks
        self.L1cache.create(options)
        self.issue_latency = 1
        self.mandatory_queue_latency = options.mandatory_queue_latency

        self.coalescer = VIPERCoalescer(ruby_system=ruby_system)
        self.coalescer.version = self.seqCount()
        self.coalescer.icache = self.L1cache
        self.coalescer.dcache = self.L1cache
        self.coalescer.ruby_system = ruby_system
        self.coalescer.support_inst_reqs = False
        self.coalescer.is_cpu_sequencer = False
        if options.tcp_deadlock_threshold:
            self.coalescer.deadlock_threshold = options.tcp_deadlock_threshold
        self.coalescer.max_coalesces_per_cycle = (
            options.max_coalesces_per_cycle
        )

        self.sequencer = RubySequencer(ruby_system=ruby_system)
        self.sequencer.version = self.seqCount()
        self.sequencer.dcache = self.L1cache
        self.sequencer.ruby_system = ruby_system
        self.sequencer.is_cpu_sequencer = True

        self.use_seq_not_coal = False

        self.ruby_system = ruby_system
        if hasattr(options, "gpu_clock") and hasattr(options, "gpu_voltage"):
            self.clk_domain = SrcClockDomain(
                clock=options.gpu_clock,
                voltage_domain=VoltageDomain(voltage=options.gpu_voltage),
            )

        if options.recycle_latency:
            self.recycle_latency = options.recycle_latency

    def createCP(self, options, ruby_system, system):
        """CPU-core variant: routes callbacks through sequencer not coalescer."""
        self.version = self.versionCount()

        self.L1cache = TCPCache(
            tagAccessLatency=options.TCP_latency,
            dataAccessLatency=options.TCP_latency,
        )
        self.L1cache.resourceStalls = options.no_resource_stalls
        self.L1cache.create(options)
        self.issue_latency = 1
        self.mandatory_queue_latency = options.mandatory_queue_latency

        self.coalescer = VIPERCoalescer(ruby_system=ruby_system)
        self.coalescer.version = self.seqCount()
        self.coalescer.icache = self.L1cache
        self.coalescer.dcache = self.L1cache
        self.coalescer.ruby_system = ruby_system
        self.coalescer.support_inst_reqs = False
        self.coalescer.is_cpu_sequencer = False

        self.sequencer = RubySequencer(ruby_system=ruby_system)
        self.sequencer.version = self.seqCount()
        self.sequencer.dcache = self.L1cache
        self.sequencer.ruby_system = ruby_system
        self.sequencer.is_cpu_sequencer = True

        self.use_seq_not_coal = True
        self.ruby_system = ruby_system

        if options.recycle_latency:
            self.recycle_latency = options.recycle_latency


class SQCCache(RubyCache):
    dataArrayBanks = 8
    tagArrayBanks = 8
    dataAccessLatency = 1
    tagAccessLatency = 1

    def create(self, options):
        self.size = MemorySize(options.sqc_size)
        self.assoc = options.sqc_assoc
        if hasattr(options, "sqc_rp"):
            self.replacement_policy = ObjectList.rp_list.get(options.sqc_rp)()


class SQCCntrl(Spandex_SQC_Controller, CntrlBase):
    """
    GPU L1 instruction cache — uses unmodified GPU_VIPER SQC controller.
    Sends CPURequestMsg to TCC on vnet 1, receives ResponseMsg on vnet 3.
    """
    def create(self, options, ruby_system, system):
        self.version = self.versionCount()

        self.L1cache = SQCCache()
        self.L1cache.create(options)
        self.L1cache.resourceStalls = options.no_resource_stalls

        self.sequencer = VIPERSequencer()
        self.sequencer.version = self.seqCount()
        self.sequencer.dcache = self.L1cache
        self.sequencer.ruby_system = ruby_system
        self.sequencer.support_data_reqs = False
        self.sequencer.is_cpu_sequencer = False
        if options.sqc_deadlock_threshold:
            self.sequencer.deadlock_threshold = options.sqc_deadlock_threshold

        self.ruby_system = ruby_system
        if hasattr(options, "gpu_clock") and hasattr(options, "gpu_voltage"):
            self.clk_domain = SrcClockDomain(
                clock=options.gpu_clock,
                voltage_domain=VoltageDomain(voltage=options.gpu_voltage),
            )

        if options.recycle_latency:
            self.recycle_latency = options.recycle_latency


class TCCCntrl(Spandex_TCC_Controller, CntrlBase):
    """
    GPU L2 shared cache — unmodified GPU_VIPER TCC controller.
    Receives CPURequestMsg from TCP/SQC on vnet 1.
    Sends CPURequestMsg to TU on vnet 0 (TCC thinks it's talking to NB/Dir).
    Receives ResponseMsg from TU on vnet 2.
    Sends UnblockMsg to TU on vnet 4.
    """
    def create(self, options, ruby_system, system):
        self.version = self.versionCount()

        self.L2cache = TCCCache()
        self.L2cache.create(options)
        self.L2cache.resourceStalls = options.no_tcc_resource_stalls

        self.ruby_system = ruby_system
        if hasattr(options, "gpu_clock") and hasattr(options, "gpu_voltage"):
            self.clk_domain = SrcClockDomain(
                clock=options.gpu_clock,
                voltage_domain=VoltageDomain(voltage=options.gpu_voltage),
            )

        if options.recycle_latency:
            self.recycle_latency = options.recycle_latency

        self.number_of_TBEs = options.num_tbes
        self.WB = options.WB_L1


class TUCntrl(Spandex_TU_Controller, CntrlBase):
    """
    Translation Unit — new controller for Architecture B.
    Sits between GPU_VIPER TCC and Spandex Directory.

    Upward  (TCC side):
      requestFromTCC  — vnet 0  CPURequestMsg  (TCC → TU)
      responseToTCC   — vnet 2  ResponseMsg    (TU  → TCC)
      unblockFromTCC  — vnet 4  UnblockMsg     (TCC → TU, absorbed)

    Downward (Spandex Dir side):
      requestToDir    — vnet 1  SpandexRequestMsg  (TU  → Dir)
      responseFromDir — vnet 3  SpandexResponseMsg (Dir → TU)
    """
    def create(self, options, ruby_system, system):
        self.version = self.versionCount()
        self.ruby_system = ruby_system
        self.number_of_TBEs = options.num_tbes

        if options.recycle_latency:
            self.recycle_latency = options.recycle_latency


class L3Cache(RubyCache):
    dataArrayBanks = 16
    tagArrayBanks = 16

    def create(self, options, ruby_system, system):
        self.size = MemorySize(options.l3_size)
        self.size.value /= options.num_dirs
        self.assoc = options.l3_assoc
        self.dataArrayBanks /= options.num_dirs
        self.tagArrayBanks /= options.num_dirs
        self.dataAccessLatency = options.l3_data_latency
        self.tagAccessLatency = options.l3_tag_latency
        self.resourceStalls = False
        self.replacement_policy = TreePLRURP()


class DirCntrl(Spandex_Directory_Controller, CntrlBase):
    """
    Spandex LLC + Directory.
    Receives SpandexRequestMsg from TU on vnet 1.
    Sends SpandexResponseMsg back to TU on vnet 3.
    """
    def create(self, options, dir_ranges, ruby_system, system):
        self.version = self.versionCount()

        self.response_latency = 30

        self.addr_ranges = dir_ranges
        self.directory = RubyDirectoryMemory(
            block_size=ruby_system.block_size_bytes
        )

        self.L3cache = L3Cache()
        self.L3cache.create(options, ruby_system, system)

        self.number_of_TBEs = options.num_tbes
        self.ruby_system = ruby_system

        if options.recycle_latency:
            self.recycle_latency = options.recycle_latency


# ── Option definitions ────────────────────────────────────────────────────────

def define_options(parser):
    parser.add_argument("--num-subcaches", type=int, default=4)
    parser.add_argument("--l3-data-latency", type=int, default=20)
    parser.add_argument("--l3-tag-latency", type=int, default=15)
    parser.add_argument("--cpu-to-dir-latency", type=int, default=120)
    parser.add_argument("--gpu-to-dir-latency", type=int, default=120)
    parser.add_argument(
        "--no-resource-stalls", action="store_false", default=True
    )
    parser.add_argument(
        "--no-tcc-resource-stalls", action="store_false", default=True
    )
    parser.add_argument("--num-tbes", type=int, default=256)
    parser.add_argument("--l2-latency", type=int, default=50)
    parser.add_argument(
        "--sqc-size", type=str, default="32KiB", help="SQC cache size"
    )
    parser.add_argument(
        "--sqc-assoc", type=int, default=8, help="SQC cache assoc"
    )
    parser.add_argument(
        "--sqc-deadlock-threshold",
        type=int,
        help="Set the SQC deadlock threshold to some value",
    )
    parser.add_argument(
        "--WB_L1", action="store_true", default=False, help="writeback L1"
    )
    parser.add_argument(
        "--TCP_latency",
        type=int,
        default=4,
        help="TCP data/tag access latency",
    )
    parser.add_argument(
        "--mandatory_queue_latency",
        type=int,
        default=1,
        help="Hit latency for TCP",
    )
    parser.add_argument(
        "--tcp-size", type=str, default="16KiB", help="TCP cache size"
    )
    parser.add_argument(
        "--tcp-assoc", type=int, default=16, help="TCP cache assoc"
    )
    parser.add_argument(
        "--tcp-deadlock-threshold",
        type=int,
        help="Set the TCP deadlock threshold to some value",
    )
    # TCC options (new for Architecture B)
    parser.add_argument(
        "--tcc-size", type=str, default="256KiB", help="TCC L2 cache size"
    )
    parser.add_argument(
        "--tcc-assoc", type=int, default=16, help="TCC L2 cache assoc"
    )
    parser.add_argument(
        "--num-tccs", type=int, default=1, help="Number of TCC controllers"
    )
    parser.add_argument(
        "--max-coalesces-per-cycle",
        type=int,
        default=1,
        help="Maximum insts that may coalesce in a cycle",
    )
    parser.add_argument(
        "--noL1", action="store_true", default=False, help="bypass L1"
    )
    parser.add_argument(
        "--tcp-num-banks",
        type=int,
        default="16",
        help="Num of banks in L1 cache",
    )
    # TCC parameters (mirroring GPU_VIPER.py so the unmodified VIPER TCC
    # controller is fully configurable from the CLI)
    parser.add_argument(
        "--TCC_latency", type=int, default=16, help="TCC latency"
    )
    parser.add_argument(
        "--WB_L2", action="store_true", default=False, help="writeback L2"
    )
    parser.add_argument(
        "--glc_atomic_latency",
        type=int, default=1,
        help="TCC atomic latency for globally-coherent atomics",
    )


# ── construct_* functions ─────────────────────────────────────────────────────

def construct_dirs(options, system, ruby_system, network):
    dir_cntrl_nodes = []

    dir_bits = int(math.log(options.num_dirs, 2))
    block_size_bits = int(math.log(options.cacheline_size, 2))

    if options.numa_high_bit:
        numa_bit = options.numa_high_bit
    else:
        numa_bit = block_size_bits + dir_bits - 1

    for i in range(options.num_dirs):
        dir_ranges = []
        for r in system.mem_ranges:
            addr_range = m5.objects.AddrRange(
                r.start,
                size=r.size(),
                intlvHighBit=numa_bit,
                intlvBits=dir_bits,
                intlvMatch=i,
            )
            dir_ranges.append(addr_range)

        dir_cntrl = DirCntrl()
        dir_cntrl.create(options, dir_ranges, ruby_system, system)
        dir_cntrl.number_of_TBEs = options.num_tbes

        # Spandex Dir receives SpandexRequestMsg from TU on vnet 1
        dir_cntrl.requestToDir = MessageBuffer(ordered=True)
        dir_cntrl.requestToDir.in_port = network.out_port

        # Spandex Dir sends SpandexResponseMsg back to TU on vnet 3
        dir_cntrl.responseFromDir = MessageBuffer()
        dir_cntrl.responseFromDir.out_port = network.in_port

        # Memory controller ports
        dir_cntrl.requestToMemory = MessageBuffer()
        dir_cntrl.responseFromMemory = MessageBuffer()

        exec("ruby_system.dir_cntrl%d = dir_cntrl" % i)
        dir_cntrl_nodes.append(dir_cntrl)

    return dir_cntrl_nodes


def construct_tcps(options, system, ruby_system, network):
    tcp_sequencers = []
    tcp_cntrl_nodes = []

    # TCC_select_num_bits selects among TCCs (not Dirs)
    tcc_bits = int(math.log(options.num_tccs, 2)) \
               if options.num_tccs > 1 else 0

    for i in range(options.num_compute_units):
        tcp_cntrl = TCPCntrl(
            TCC_select_num_bits=tcc_bits,
            issue_latency=1,
            number_of_TBEs=2560,
        )
        tcp_cntrl.create(options, ruby_system, system)
        tcp_cntrl.WB = options.WB_L1
        tcp_cntrl.disableL1 = options.noL1
        tcp_cntrl.L1cache.tagAccessLatency = options.TCP_latency
        tcp_cntrl.L1cache.dataAccessLatency = options.TCP_latency

        exec("ruby_system.tcp_cntrl%d = tcp_cntrl" % i)

        tcp_sequencers.append(tcp_cntrl.coalescer)
        tcp_cntrl_nodes.append(tcp_cntrl)

        # TCP → TCC: CPURequestMsg on vnet 1
        tcp_cntrl.requestFromTCP = MessageBuffer(ordered=True)
        tcp_cntrl.requestFromTCP.out_port = network.in_port

        # TCP → TCC: outbound response on vnet 3 (used for some flows)
        tcp_cntrl.responseFromTCP = MessageBuffer(ordered=True)
        tcp_cntrl.responseFromTCP.out_port = network.in_port

        # TCP → TCC: unblock on vnet 5
        tcp_cntrl.unblockFromCore = MessageBuffer()
        tcp_cntrl.unblockFromCore.out_port = network.in_port

        # TCC → TCP: probe on vnet 1 (inbound)
        tcp_cntrl.probeToTCP = MessageBuffer(ordered=True)
        tcp_cntrl.probeToTCP.in_port = network.out_port

        # TCC → TCP: ResponseMsg on vnet 3 (inbound)
        tcp_cntrl.responseToTCP = MessageBuffer(ordered=True)
        tcp_cntrl.responseToTCP.in_port = network.out_port

        tcp_cntrl.mandatoryQueue = MessageBuffer()

    return (tcp_sequencers, tcp_cntrl_nodes)


def construct_sqcs(options, system, ruby_system, network):
    sqc_sequencers = []
    sqc_cntrl_nodes = []

    tcc_bits = int(math.log(options.num_tccs, 2)) \
               if options.num_tccs > 1 else 0

    for i in range(options.num_sqc):
        sqc_cntrl = SQCCntrl(TCC_select_num_bits=tcc_bits)
        sqc_cntrl.create(options, ruby_system, system)

        exec("ruby_system.sqc_cntrl%d = sqc_cntrl" % i)

        sqc_sequencers.append(sqc_cntrl.sequencer)
        sqc_cntrl_nodes.append(sqc_cntrl)

        # SQC → TCC: CPURequestMsg on vnet 1
        sqc_cntrl.requestFromSQC = MessageBuffer(ordered=True)
        sqc_cntrl.requestFromSQC.out_port = network.in_port

        # TCC → SQC: probe on vnet 1 (inbound)
        sqc_cntrl.probeToSQC = MessageBuffer(ordered=True)
        sqc_cntrl.probeToSQC.in_port = network.out_port

        # TCC → SQC: ResponseMsg on vnet 3 (inbound)
        sqc_cntrl.responseToSQC = MessageBuffer(ordered=True)
        sqc_cntrl.responseToSQC.in_port = network.out_port

        sqc_cntrl.mandatoryQueue = MessageBuffer()

    return (sqc_sequencers, sqc_cntrl_nodes)


def construct_tccs(options, system, ruby_system, network):
    """
    Create GPU_VIPER TCC (L2) controllers.
    TCC sits between TCP/SQC and the TU.
    Its NB-facing ports (vnet 0/2/4) are connected to the network;
    the TU intercepts those messages.
    """
    tcc_cntrl_nodes = []

    tcc_bits = int(math.log(options.num_tccs, 2)) \
               if options.num_tccs > 1 else 0

    for i in range(options.num_tccs):
        # Match GPU_VIPER.py's TCC instantiation.
        tcc_cntrl = TCCCntrl(l2_response_latency=options.TCC_latency)
        tcc_cntrl.create(options, ruby_system, system)
        tcc_cntrl.l2_request_latency = options.gpu_to_dir_latency
        tcc_cntrl.l2_response_latency = options.TCC_latency
        tcc_cntrl.glc_atomic_latency = options.glc_atomic_latency
        tcc_cntrl.WB = options.WB_L2
        tcc_cntrl.number_of_TBEs = 2560 * options.num_compute_units

        # TCP/SQC → TCC: CPURequestMsg on vnet 1
        tcc_cntrl.requestFromTCP = MessageBuffer(ordered=True)
        tcc_cntrl.requestFromTCP.in_port = network.out_port

        # TCC → TCP/SQC: ResponseMsg on vnet 3
        tcc_cntrl.responseToCore = MessageBuffer(ordered=True)
        tcc_cntrl.responseToCore.out_port = network.in_port

        # TU → TCC: probes (vnet 0). Unused in our flow but must be wired.
        tcc_cntrl.probeFromNB = MessageBuffer()
        tcc_cntrl.probeFromNB.in_port = network.out_port

        # TU → TCC: data response (vnet 2). Synthesized by TU from Spandex Dir.
        tcc_cntrl.responseFromNB = MessageBuffer()
        tcc_cntrl.responseFromNB.in_port = network.out_port

        # TCC → TU: outbound request (vnet 0). Originally requestToNB to dir.
        tcc_cntrl.requestToNB = MessageBuffer(ordered=True)
        tcc_cntrl.requestToNB.out_port = network.in_port

        # TCC → TU: probe ack (vnet 2). Unused but must be wired.
        tcc_cntrl.responseToNB = MessageBuffer()
        tcc_cntrl.responseToNB.out_port = network.in_port

        # TCC → TU: unblock (vnet 4). TU absorbs silently.
        tcc_cntrl.unblockToNB = MessageBuffer()
        tcc_cntrl.unblockToNB.out_port = network.in_port

        # Internal trigger queue (atomic ALU)
        tcc_cntrl.triggerQueue = MessageBuffer(ordered=True)

        exec("ruby_system.tcc_cntrl%d = tcc_cntrl" % i)
        tcc_cntrl_nodes.append(tcc_cntrl)

    return tcc_cntrl_nodes


def construct_tus(options, system, ruby_system, network):
    """
    Create Translation Unit controllers — one per TCC.
    The TU intercepts TCC's NB-facing messages and translates them
    to/from Spandex Directory vocabulary.

    Upward  (TCC side, vnets 0/2/4):
      requestFromTCC  vnet 0 in  — CPURequestMsg from TCC
      responseToTCC   vnet 2 out — ResponseMsg to TCC
      unblockFromTCC  vnet 4 in  — UnblockMsg from TCC (absorbed)
      probeToTCC      vnet 0 out — NBProbeRequestMsg (unused stub)
      probeAckFromTCC vnet 2 in  — ResponseMsg probe ack (unused stub)

    Downward (Spandex Dir side, vnets 1/3):
      requestToDir    vnet 1 out — SpandexRequestMsg to Spandex Dir
      responseFromDir vnet 3 in  — SpandexResponseMsg from Spandex Dir
    """
    tu_cntrl_nodes = []

    for i in range(options.num_tccs):
        tu_cntrl = TUCntrl(
            TCC_select_num_bits=0,   # TU is 1:1 with Dir, no selection needed
            number_of_TBEs=options.num_tbes,
        )
        tu_cntrl.create(options, ruby_system, system)

        exec("ruby_system.tu_cntrl%d = tu_cntrl" % i)
        tu_cntrl_nodes.append(tu_cntrl)

        # ── Upward ports (TCC side) ──────────────────────────────────────

        # TCC → TU: CPURequestMsg on vnet 0
        tu_cntrl.requestFromTCC = MessageBuffer(ordered=True)
        tu_cntrl.requestFromTCC.in_port = network.out_port

        # TU → TCC: ResponseMsg on vnet 2
        tu_cntrl.responseToTCC = MessageBuffer(ordered=True)
        tu_cntrl.responseToTCC.out_port = network.in_port

        # TCC → TU: UnblockMsg on vnet 4 (absorbed silently by TU)
        tu_cntrl.unblockFromTCC = MessageBuffer()
        tu_cntrl.unblockFromTCC.in_port = network.out_port

        # TU → TCC: NBProbeRequestMsg on vnet 0 (unused stub)
        tu_cntrl.probeToTCC = MessageBuffer()
        tu_cntrl.probeToTCC.out_port = network.in_port

        # # TCC → TU: probe ack ResponseMsg on vnet 2 (unused stub)
        # tu_cntrl.probeAckFromTCC = MessageBuffer()
        # tu_cntrl.probeAckFromTCC.in_port = network.out_port

        # ── Downward ports (Spandex Dir side) ───────────────────────────

        # TU → Spandex Dir: SpandexRequestMsg on vnet 1
        tu_cntrl.requestToDir = MessageBuffer(ordered=True)
        tu_cntrl.requestToDir.out_port = network.in_port

        # Spandex Dir → TU: SpandexResponseMsg on vnet 3
        tu_cntrl.responseFromDir = MessageBuffer(ordered=True)
        tu_cntrl.responseFromDir.in_port = network.out_port

    return tu_cntrl_nodes


def construct_cpus(options, system, ruby_system, network):
    cpu_sequencers_local = []
    cpu_cntrl_nodes = []

    tcc_bits = int(math.log(options.num_tccs, 2)) \
               if options.num_tccs > 1 else 0

    num_cpus = getattr(options, "num_cpus", 0)
    for i in range(num_cpus):
        cpu_cntrl = TCPCntrl(
            TCC_select_num_bits=tcc_bits,
            issue_latency=1,
            number_of_TBEs=256,
        )
        cpu_cntrl.createCP(options, ruby_system, system)
        cpu_cntrl.WB = options.WB_L1
        cpu_cntrl.disableL1 = options.noL1

        exec("ruby_system.cpu_cntrl%d = cpu_cntrl" % i)

        cpu_sequencers_local.append(cpu_cntrl.sequencer)
        cpu_cntrl_nodes.append(cpu_cntrl)

        # Match the GPU TCP wiring exactly — TCP machine is the same SLICC
        # machine for both GPU and CPU modes (TCP-as-CPU pattern), so it
        # declares the full set of MessageBuffer ports.
        cpu_cntrl.requestFromTCP = MessageBuffer(ordered=True)
        cpu_cntrl.requestFromTCP.out_port = network.in_port

        cpu_cntrl.responseFromTCP = MessageBuffer(ordered=True)
        cpu_cntrl.responseFromTCP.out_port = network.in_port

        cpu_cntrl.unblockFromCore = MessageBuffer()
        cpu_cntrl.unblockFromCore.out_port = network.in_port

        cpu_cntrl.probeToTCP = MessageBuffer(ordered=True)
        cpu_cntrl.probeToTCP.in_port = network.out_port

        cpu_cntrl.responseToTCP = MessageBuffer(ordered=True)
        cpu_cntrl.responseToTCP.in_port = network.out_port

        cpu_cntrl.mandatoryQueue = MessageBuffer()

    return (cpu_sequencers_local, cpu_cntrl_nodes)


def construct_dmas(options, system, ruby_system, network, dma_devices):
    dma_cntrl_nodes = []

    for i, dma_device in enumerate(dma_devices):
        dma_seq = DMASequencer(version=i, ruby_system=ruby_system)
        dma_cntrl = Spandex_DMA_Controller(
            version=i,
            dma_sequencer=dma_seq,
            ruby_system=ruby_system,
            number_of_TBEs=16,
        )

        exec("system.dma_cntrl%d = dma_cntrl" % i)

        if not hasattr(dma_device, "type"):
            exec("system.dma_cntrl%d.dma_sequencer.in_ports = dma_device" % i)
        elif dma_device.type == "MemTest":
            exec(
                "system.dma_cntrl%d.dma_sequencer.in_ports = dma_devices.test"
                % i
            )
        else:
            exec(
                "system.dma_cntrl%d.dma_sequencer.in_ports = dma_device.dma"
                % i
            )

        dma_cntrl.requestToDir = MessageBuffer(buffer_size=0)
        dma_cntrl.requestToDir.out_port = network.in_port
        dma_cntrl.responseFromDir = MessageBuffer(buffer_size=0)
        dma_cntrl.responseFromDir.in_port = network.out_port
        dma_cntrl.mandatoryQueue = MessageBuffer(buffer_size=0)

        dma_cntrl_nodes.append(dma_cntrl)

    return dma_cntrl_nodes


def construct_scalars(options, system, ruby_system, network):
    scalar_sequencers = []
    scalar_cntrl_nodes = []

    tcc_bits = int(math.log(options.num_tccs, 2)) \
               if options.num_tccs > 1 else 0

    for i in range(options.num_scalar_cache):
        scalar_cntrl = SQCCntrl(TCC_select_num_bits=tcc_bits)
        scalar_cntrl.create(options, ruby_system, system)

        exec("ruby_system.scalar_cntrl%d = scalar_cntrl" % i)

        scalar_sequencers.append(scalar_cntrl.sequencer)
        scalar_cntrl_nodes.append(scalar_cntrl)

        scalar_cntrl.requestFromSQC = MessageBuffer(ordered=True)
        scalar_cntrl.requestFromSQC.out_port = network.in_port

        scalar_cntrl.probeToSQC = MessageBuffer(ordered=True)
        scalar_cntrl.probeToSQC.in_port = network.out_port

        scalar_cntrl.responseToSQC = MessageBuffer(ordered=True)
        scalar_cntrl.responseToSQC.in_port = network.out_port

        scalar_cntrl.mandatoryQueue = MessageBuffer()

    return (scalar_sequencers, scalar_cntrl_nodes)


# ── create_system ─────────────────────────────────────────────────────────────

def create_system(
    options, full_system, system, dma_devices, bootmem, ruby_system, cpus
):
    if buildEnv["PROTOCOL"] != "Spandex":
        panic("This script requires the Spandex protocol to be built.")

    cpu_sequencers = []

    mainCluster = Cluster(intBW=8)
    cpuCluster  = Cluster(extBW=8, intBW=8)
    gpuCluster  = Cluster(extBW=8, intBW=8)

    # ── Spandex Directory (LLC) ──────────────────────────────────────────
    dir_cntrl_nodes = construct_dirs(
        options, system, ruby_system, ruby_system.network
    )
    for dir_cntrl in dir_cntrl_nodes:
        mainCluster.add(dir_cntrl)

    # ── Translation Units (one per TCC) ─────────────────────────────────
    # Add TUs before TCCs so they are registered with the network first.
    # This ensures the TU MachineIDs are known before TCC routing is set up.
    tu_cntrl_nodes = construct_tus(
        options, system, ruby_system, ruby_system.network
    )
    for tu_cntrl in tu_cntrl_nodes:
        gpuCluster.add(tu_cntrl)

    # ── GPU_VIPER TCC (L2) ───────────────────────────────────────────────
    tcc_cntrl_nodes = construct_tccs(
        options, system, ruby_system, ruby_system.network
    )
    for tcc_cntrl in tcc_cntrl_nodes:
        gpuCluster.add(tcc_cntrl)

    # ── CPU controllers ──────────────────────────────────────────────────
    # Created before GPU TCP so CPU sequencers come first in cpu_sequencers
    # (apu_se.py expects _cpu_ports[0..num_cpus-1] to be CPU sequencers)
    (cpu_seqs, cpu_cntrl_nodes) = construct_cpus(
        options, system, ruby_system, ruby_system.network
    )
    cpu_sequencers.extend(cpu_seqs)
    for cpu_cntrl in cpu_cntrl_nodes:
        cpuCluster.add(cpu_cntrl)

    # ── GPU TCP (L1 data caches) ─────────────────────────────────────────
    (tcp_sequencers, tcp_cntrl_nodes) = construct_tcps(
        options, system, ruby_system, ruby_system.network
    )
    cpu_sequencers.extend(tcp_sequencers)
    for tcp_cntrl in tcp_cntrl_nodes:
        gpuCluster.add(tcp_cntrl)

    # ── GPU SQC (L1 instruction caches) ─────────────────────────────────
    (sqc_sequencers, sqc_cntrl_nodes) = construct_sqcs(
        options, system, ruby_system, ruby_system.network
    )
    cpu_sequencers.extend(sqc_sequencers)
    for sqc_cntrl in sqc_cntrl_nodes:
        gpuCluster.add(sqc_cntrl)

    # ── Scalar caches (reuse SQC controller) ────────────────────────────
    (scalar_sequencers, scalar_cntrl_nodes) = construct_scalars(
        options, system, ruby_system, ruby_system.network
    )
    cpu_sequencers.extend(scalar_sequencers)
    for scalar_cntrl in scalar_cntrl_nodes:
        gpuCluster.add(scalar_cntrl)

    # ── DMA controllers ──────────────────────────────────────────────────
    dma_cntrl_nodes = construct_dmas(
        options, system, ruby_system, ruby_system.network, dma_devices
    )
    for dma_cntrl in dma_cntrl_nodes:
        gpuCluster.add(dma_cntrl)

    mainCluster.add(cpuCluster)
    mainCluster.add(gpuCluster)

    # vnets used:
    #   0 — TCC → TU requests  /  TU → TCC probes (stub)
    #   1 — TU  → Dir requests /  TCP/SQC → TCC requests
    #   2 — TU  → TCC responses / TCC → TU probe acks (stub)
    #   3 — Dir → TU responses /  TCC → TCP/SQC responses
    #   4 — TCC → TU unblock (absorbed by TU)
    # vnet 0 = TCC↔TU + (CorePair) request
    # vnet 1 = TCP/SQC↔TCC request + probe
    # vnet 2 = TCC↔TU + (CorePair) response
    # vnet 3 = TCC↔TCP/SQC response + TU↔Dir (Spandex) response
    # vnet 4 = unblock from TCC
    # vnet 5 = TCP unblock (declared by GPU_VIPER-TCP.sm)
    ruby_system.network.number_of_virtual_networks = 6

    return (cpu_sequencers, dir_cntrl_nodes, mainCluster)