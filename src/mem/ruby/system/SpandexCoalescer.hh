// Spandex Coalescer Header

#ifndef __MEM_RUBY_SYSTEM_SPANDEXCOALESCER_HH__
#define __MEM_RUBY_SYSTEM_SPANDEXCOALESCER_HH__

#include "mem/ruby/system/VIPERCoalescer.hh"
#include "params/SpandexCoalescer.hh"

namespace gem5
{

namespace ruby
{

class SpandexCoalescer : public VIPERCoalescer
{
  public:
    typedef SpandexCoalescerParams Params;
    SpandexCoalescer(const Params &p);
    ~SpandexCoalescer();
};

} // namespace ruby
} // namespace gem5

#endif // __MEM_RUBY_SYSTEM_SPANDEXCOALESCER_HH__
