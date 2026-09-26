// A local battle has one ordered consumer. Send each native ring record once;
// retain the original ring counters so the reader still detects overwritten
// or missing events. Other observation routes continue to receive full rings.
struct LocalEventCursor {
  std::uint64_t generation=0, epoch=0, next=0;
};
struct LocalEventDelta { LocalEventCursor rings[4]; };
thread_local LocalEventDelta* g_local_event_delta_capture=nullptr;

std::uint64_t local_event_transmit_from(int ring,std::uint64_t generation,std::uint64_t epoch,
                                       std::uint64_t oldest,std::uint64_t next) {
  if(!g_local_event_delta_capture) return oldest;
  auto& cursor=g_local_event_delta_capture->rings[ring];
  const bool same=cursor.next && cursor.generation==generation && cursor.epoch==epoch;
  const std::uint64_t from=same?std::max(oldest,cursor.next):oldest;
  cursor={generation,epoch,next};
  return from<=next?from:oldest;
}

struct LocalEventDeltaScope {
  LocalEventDelta* previous;
  explicit LocalEventDeltaScope(LocalEventDelta& value):previous(g_local_event_delta_capture) {
    g_local_event_delta_capture=&value;
  }
  ~LocalEventDeltaScope() { g_local_event_delta_capture=previous; }
};
