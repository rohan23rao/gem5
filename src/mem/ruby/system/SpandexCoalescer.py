# Spandex Coalescer SimObject Definition

from m5.objects.VIPERCoalescer import VIPERCoalescer
from m5.params import *
from m5.proxy import *


class SpandexCoalescer(VIPERCoalescer):
    type = "SpandexCoalescer"
    cxx_class = "gem5::ruby::SpandexCoalescer"
    cxx_header = "mem/ruby/system/SpandexCoalescer.hh"
