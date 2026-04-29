# Spandex Coherence Protocol Configuration
#
# Based on GPU_VIPER.py but simplified:
# - No TCC (GPU L2) -- TCP talks directly to directory
# - Directory has built-in L3 cache (no separate L3Cache controller)
# - SpandexCoalescer instead of VIPERCoalescer
# - No CPU CorePair or DMA controllers (GPU-only for now)

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


class TCPCntrl(Spandex_TCP_Controller, CntrlBase):
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

        self.coalescer = SpandexCoalescer(ruby_system=ruby_system)
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

    # Variant used for host CPU cores: RubySequencer is the primary port,
    # VIPERCoalescer is still instantiated because the TCP machine requires
    # it, but use_seq_not_coal routes all callbacks through the sequencer.
    def createCP(self, options, ruby_system, system):
        self.version = self.versionCount()

        self.L1cache = TCPCache(
            tagAccessLatency=options.TCP_latency,
            dataAccessLatency=options.TCP_latency,
        )
        self.L1cache.resourceStalls = options.no_resource_stalls
        self.L1cache.create(options)
        self.issue_latency = 1
        self.mandatory_queue_latency = options.mandatory_queue_latency

        self.coalescer = SpandexCoalescer(ruby_system=ruby_system)
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


class L3Cache(RubyCache):
    dataArrayBanks = 16
    tagArrayBanks = 16

    def create(self, options, ruby_system, system):
        self.size = MemorySize(options.l3_size)
        self.size.value /= options.num_dirs
        self.assoc = options.l3_assoc
        self.dataArrayBanks /= options.num_dirs
        self.tagArrayBanks /= options.num_dirs
        self.dataArrayBanks /= options.num_dirs
        self.tagArrayBanks /= options.num_dirs
        self.dataAccessLatency = options.l3_data_latency
        self.tagAccessLatency = options.l3_tag_latency
        self.resourceStalls = False
        self.replacement_policy = TreePLRURP()


class DirCntrl(Spandex_Directory_Controller, CntrlBase):
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
    parser.add_argument(
        "--l2-latency", type=int, default=50,
    )
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
        "--tcp-size", type=str, default="16KiB", help="tcp size"
    )
    parser.add_argument("--tcp-assoc", type=int, default=16, help="tcp assoc")
    parser.add_argument(
        "--tcp-deadlock-threshold",
        type=int,
        help="Set the TCP deadlock threshold to some value",
    )
    parser.add_argument(
        "--max-coalesces-per-cycle",
        type=int,
        default=1,
        help="Maximum insts that may coalesce in a cycle",
    )
    parser.add_argument(
        "--noL1", action="store_true", default=False, help="bypassL1"
    )
    parser.add_argument(
        "--tcp-num-banks",
        type=int,
        default="16",
        help="Num of banks in L1 cache",
    )


def construct_dirs(options, system, ruby_system, network):
    dir_cntrl_nodes = []

    # Number of bits to select among directories
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

        # Connect directory to network
        # requestToDir: incoming requests from TCP/SQC (vnet 1)
        dir_cntrl.requestToDir = MessageBuffer(ordered=True)
        dir_cntrl.requestToDir.in_port = network.out_port

        # responseFromDir: outgoing responses to TCP/SQC (vnet 3)
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

    # Use dir_bits for address-to-directory mapping (no TCC)
    dir_bits = int(math.log(options.num_dirs, 2))

    for i in range(options.num_compute_units):
        tcp_cntrl = TCPCntrl(
            TCC_select_num_bits=dir_bits,
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

        # Connect TCP to network
        tcp_cntrl.requestFromTCP = MessageBuffer(ordered=True)
        tcp_cntrl.requestFromTCP.out_port = network.in_port

        tcp_cntrl.responseToTCP = MessageBuffer(ordered=True)
        tcp_cntrl.responseToTCP.in_port = network.out_port

        tcp_cntrl.mandatoryQueue = MessageBuffer()

    return (tcp_sequencers, tcp_cntrl_nodes)


def construct_sqcs(options, system, ruby_system, network):
    sqc_sequencers = []
    sqc_cntrl_nodes = []

    dir_bits = int(math.log(options.num_dirs, 2))

    for i in range(options.num_sqc):
        sqc_cntrl = SQCCntrl(TCC_select_num_bits=dir_bits)
        sqc_cntrl.create(options, ruby_system, system)

        exec("ruby_system.sqc_cntrl%d = sqc_cntrl" % i)

        sqc_sequencers.append(sqc_cntrl.sequencer)
        sqc_cntrl_nodes.append(sqc_cntrl)

        # Connect SQC to network
        sqc_cntrl.requestFromSQC = MessageBuffer(ordered=True)
        sqc_cntrl.requestFromSQC.out_port = network.in_port

        sqc_cntrl.responseToSQC = MessageBuffer(ordered=True)
        sqc_cntrl.responseToSQC.in_port = network.out_port

        sqc_cntrl.mandatoryQueue = MessageBuffer()

    return (sqc_sequencers, sqc_cntrl_nodes)


def construct_cpus(options, system, ruby_system, network):
    cpu_sequencers_local = []
    cpu_cntrl_nodes = []

    dir_bits = int(math.log(options.num_dirs, 2))

    num_cpus = getattr(options, "num_cpus", 0)
    for i in range(num_cpus):
        cpu_cntrl = TCPCntrl(
            TCC_select_num_bits=dir_bits,
            issue_latency=1,
            number_of_TBEs=256,
        )
        cpu_cntrl.createCP(options, ruby_system, system)
        cpu_cntrl.WB = options.WB_L1
        cpu_cntrl.disableL1 = options.noL1

        exec("ruby_system.cpu_cntrl%d = cpu_cntrl" % i)

        cpu_sequencers_local.append(cpu_cntrl.sequencer)
        cpu_cntrl_nodes.append(cpu_cntrl)

        cpu_cntrl.requestFromTCP = MessageBuffer(ordered=True)
        cpu_cntrl.requestFromTCP.out_port = network.in_port

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

    dir_bits = int(math.log(options.num_dirs, 2))

    for i in range(options.num_scalar_cache):
        scalar_cntrl = SQCCntrl(TCC_select_num_bits=dir_bits)
        scalar_cntrl.create(options, ruby_system, system)

        exec("ruby_system.scalar_cntrl%d = scalar_cntrl" % i)

        scalar_sequencers.append(scalar_cntrl.sequencer)
        scalar_cntrl_nodes.append(scalar_cntrl)

        scalar_cntrl.requestFromSQC = MessageBuffer(ordered=True)
        scalar_cntrl.requestFromSQC.out_port = network.in_port

        scalar_cntrl.responseToSQC = MessageBuffer(ordered=True)
        scalar_cntrl.responseToSQC.in_port = network.out_port

        scalar_cntrl.mandatoryQueue = MessageBuffer()

    return (scalar_sequencers, scalar_cntrl_nodes)


def create_system(
    options, full_system, system, dma_devices, bootmem, ruby_system, cpus
):
    if buildEnv["PROTOCOL"] != "Spandex":
        panic("This script requires the Spandex protocol to be built.")

    cpu_sequencers = []

    mainCluster = Cluster(intBW=8)
    cpuCluster = Cluster(extBW=8, intBW=8)
    gpuCluster = Cluster(extBW=8, intBW=8)

    # Create directory controllers (LLC + directory combined)
    dir_cntrl_nodes = construct_dirs(
        options, system, ruby_system, ruby_system.network
    )
    for dir_cntrl in dir_cntrl_nodes:
        mainCluster.add(dir_cntrl)

    # Create CPU controllers FIRST so their sequencers come before GPU ports
    # (apu_se.py expects _cpu_ports[0..num_cpus-1] to be CPU sequencers)
    (cpu_seqs, cpu_cntrl_nodes) = construct_cpus(
        options, system, ruby_system, ruby_system.network
    )
    cpu_sequencers.extend(cpu_seqs)
    for cpu_cntrl in cpu_cntrl_nodes:
        cpuCluster.add(cpu_cntrl)

    # Create TCPs (GPU L1 data caches)
    (tcp_sequencers, tcp_cntrl_nodes) = construct_tcps(
        options, system, ruby_system, ruby_system.network
    )
    cpu_sequencers.extend(tcp_sequencers)
    for tcp_cntrl in tcp_cntrl_nodes:
        gpuCluster.add(tcp_cntrl)

    # Create SQCs (GPU L1 instruction caches)
    (sqc_sequencers, sqc_cntrl_nodes) = construct_sqcs(
        options, system, ruby_system, ruby_system.network
    )
    cpu_sequencers.extend(sqc_sequencers)
    for sqc_cntrl in sqc_cntrl_nodes:
        gpuCluster.add(sqc_cntrl)

    # Create Scalar caches (reuse SQC controller)
    (scalar_sequencers, scalar_cntrl_nodes) = construct_scalars(
        options, system, ruby_system, ruby_system.network
    )
    cpu_sequencers.extend(scalar_sequencers)
    for scalar_cntrl in scalar_cntrl_nodes:
        gpuCluster.add(scalar_cntrl)

    # DMA controllers (HSA packet processor, GPU command processor in apu_se.py)
    dma_cntrl_nodes = construct_dmas(
        options, system, ruby_system, ruby_system.network, dma_devices
    )
    for dma_cntrl in dma_cntrl_nodes:
        gpuCluster.add(dma_cntrl)

    # No TCC -- TCP talks directly to directory
    mainCluster.add(cpuCluster)
    mainCluster.add(gpuCluster)

    # We use vnets 1 (requests) and 3 (responses), so need at least 4
    ruby_system.network.number_of_virtual_networks = 4

    return (cpu_sequencers, dir_cntrl_nodes, mainCluster)
