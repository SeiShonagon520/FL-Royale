#include "cold_clone_diagnostic.h"

#if defined(FIRSTLIGHT_ENABLE_COLD_CLONE_DIAGNOSTIC) && FIRSTLIGHT_ENABLE_COLD_CLONE_DIAGNOSTIC
#include <atomic>
#include <cstring>

namespace firstlight::cold_clone {
namespace {
std::atomic<bool> attempted{false};
constexpr std::uint32_t kMaxPayload = 32u * 1024u * 1024u;

template <class T>
bool Read(const Api& api, const void* object, std::size_t offset, T& value) {
  return object && api.copy_readable(static_cast<const std::uint8_t*>(object) + offset,
                                     &value, sizeof(value));
}

struct Graph {
  void* queue = nullptr;
  void* world = nullptr;
  void* objects = nullptr;
  void* players[2]{};
  void* decks[2]{};
  void* slots[2]{};
  void* hands[2]{};
  std::int32_t state = -1;
  std::int32_t manager_state = -1;
  std::uint8_t ready = 0;
};

bool Vector(const Api& api, void* object, std::size_t off, std::int32_t expected,
            std::size_t element_size, void*& data) {
  std::int32_t capacity = -1, count = -1;
  if (!Read(api, object, off, data) || !Read(api, object, off + 8, capacity) ||
      !Read(api, object, off + 12, count) || !data || count != expected ||
      capacity < count || capacity > 32) return false;
  std::uint8_t scratch[8 * sizeof(void*)]{};
  return api.copy_readable(data, scratch, element_size * static_cast<std::size_t>(count));
}

bool Inspect(const Api& api, void* manager, Graph& graph) {
  void* backref = nullptr;
  std::int32_t owners = -1;
  if (!Read(api, manager, 0x38, graph.queue) || !Read(api, manager, 0x40, graph.objects) ||
      !Read(api, manager, 0xa8, graph.world) || !graph.objects || !graph.world ||
      !Read(api, manager, 0x24, graph.manager_state) ||
      !Read(api, manager, 0x1ec, graph.ready) ||
      !Read(api, graph.queue, 0x20, backref) || backref != manager ||
      !Read(api, graph.world, 0x18, graph.state) ||
      !Read(api, graph.world, 0x60, owners) || owners != 2) return false;
  for (unsigned owner = 0; owner != 2; ++owner) {
    if (!Read(api, graph.world, 0xe0 + owner * sizeof(void*), graph.players[owner]) ||
        !Read(api, graph.world, 0x88 + owner * sizeof(void*), graph.decks[owner]) ||
        !Vector(api, graph.decks[owner], 0x20, 8, sizeof(void*), graph.slots[owner]) ||
        !Vector(api, graph.players[owner], 0x220, 4, sizeof(std::int32_t), graph.hands[owner]))
      return false;
  }
  return true;
}

bool Distinct(const Graph& a, const Graph& b) {
  const void* first[]{a.queue,a.world,a.objects,a.players[0],a.players[1],a.decks[0],
                      a.decks[1],a.slots[0],a.slots[1],a.hands[0],a.hands[1]};
  const void* second[]{b.queue,b.world,b.objects,b.players[0],b.players[1],b.decks[0],
                       b.decks[1],b.slots[0],b.slots[1],b.hands[0],b.hands[1]};
  for (const void* x : first) for (const void* y : second) if (x == y) return false;
  return true; // Root-level mutable graph check, not exhaustive alias analysis.
}

bool SnapshotDigest(const Api& api, void* packet, std::uint32_t& bytes, std::uint64_t& digest) {
  std::int32_t count = -1;
  const std::uint8_t* data = nullptr;
  if (!Read(api, packet, 0x1c, count) || !Read(api, packet, 0x40, data) ||
      count < 3 || static_cast<std::uint32_t>(count) > kMaxPayload || !data) return false;
  bytes = static_cast<std::uint32_t>(count);
  digest = 1469598103934665603ULL; // Matches current probe diagnostic digest.
  std::uint8_t block[1024]{};
  for (std::uint32_t offset = 0; offset < bytes;) {
    const std::size_t size = bytes - offset < sizeof(block) ? bytes - offset : sizeof(block);
    if (!api.copy_readable(data + offset, block, size)) return false;
    for (std::size_t i = 0; i < size; ++i) { digest ^= block[i]; digest *= 1099511628211ULL; }
    offset += static_cast<std::uint32_t>(size);
  }
  return true;
}

struct Owned {
  const Api& api;
  void* source = nullptr;
  void* destination = nullptr;
  void* packet = nullptr;
  void* verify = nullptr;
  void DropManager(void*& manager) {
    if (!manager) return;
    api.destroy(manager); api.destroy_base(manager); api.deallocate(manager); manager = nullptr;
  }
  void DropPacket(void*& wrapper) {
    if (wrapper) { api.delete_snapshot(wrapper); wrapper = nullptr; }
  }
  void Cleanup() {
    DropPacket(verify); DropPacket(packet); DropManager(destination); DropManager(source);
  }
};
} // namespace

Report Run(const Api& api, const Request& request) {
  Report report;
  if (!request.enabled) return report;
  if (!api.isolated || !api.copy_readable || !api.allocate || !api.construct || !api.setup ||
      !api.original_load || !api.destroy || !api.destroy_base || !api.deallocate ||
      !api.snapshot || !api.delete_snapshot || !api.flip_read || !api.rewind_read ||
      !api.read_byte || !api.read_boolean || !api.read_header || !api.read_body ||
      !api.tick || !api.original_step || !api.semantic_digest || !request.context_a ||
      !request.context_b || !request.parsed_match_json || request.steps_after_source_release > 20 ||
      (request.exercise_commands && !api.exercise_commands)) {
    report.code = Code::InvalidApi; return report;
  }
  if (!api.isolated()) { report.code = Code::IsolationLost; return report; }
  if (attempted.exchange(true)) { report.code = Code::AlreadyAttempted; return report; }
  report.code = Code::Running;
  Owned owned{api};
  const auto checkpoint = [&](Stage stage) {
    report.stage = stage;
    if (api.progress) api.progress(report);
    return api.isolated();
  };
  const auto fail = [&](Code code) {
    report.code = code;
    // Report the first failed stage before any potentially faulting destructor.
    if (api.progress) api.progress(report);
    owned.Cleanup(); report.cleaned_up = true; return report;
  };
  owned.source = api.allocate(0x230);
  if (!owned.source) return fail(Code::AllocationFailed);
  api.construct(owned.source, 1, request.context_a, request.context_b, 0);
  if (!checkpoint(Stage::SourceConstructed)) return fail(Code::IsolationLost);
  api.setup(owned.source, 1);
  api.original_load(owned.source, request.parsed_match_json, nullptr, nullptr);
  if (!checkpoint(Stage::SourceLoaded)) return fail(Code::IsolationLost);
  Graph source;
  std::uint64_t source_semantic = 0;
  report.source_tick = api.tick(owned.source);
  if (report.source_tick != 0 || !Inspect(api, owned.source, source) || source.state != 3 ||
      !api.semantic_digest(owned.source, &source_semantic)) return fail(Code::SourceInvalid);
  report.source_manager_state = source.manager_state;
  report.world_state = source.state;
  api.snapshot(owned.source, &owned.packet);
  if (!SnapshotDigest(api, owned.packet, report.payload_bytes, report.source_digest))
    return fail(Code::SnapshotInvalid);
  if (!checkpoint(Stage::SnapshotWritten)) return fail(Code::IsolationLost);
  void* const stream = static_cast<std::uint8_t*>(owned.packet) + 8;
  api.flip_read(stream);
  owned.destination = api.allocate(0x230);
  if (!owned.destination) return fail(Code::AllocationFailed);
  api.construct(owned.destination, 1, request.context_a, request.context_b, 0);
  void* cold_world = nullptr;
  void* cold_location = nullptr;
  if (!Read(api, owned.destination, 0xa8, cold_world) || !cold_world ||
      !Read(api, cold_world, 0x10, cold_location) || cold_location)
    return fail(Code::DestinationNotCold);
  if (!checkpoint(Stage::DestinationConstructed)) return fail(Code::IsolationLost);
  api.rewind_read(stream);
  if ((api.read_byte(stream) & 255) != 0 || (api.read_byte(stream) & 255) != 0 ||
      (api.read_boolean(stream) & 1) == 0) return fail(Code::StreamInvalid);
  api.read_header(owned.destination, stream);
  if (!checkpoint(Stage::HeaderRead)) return fail(Code::IsolationLost);
  api.read_body(owned.destination, stream, 1, nullptr);
  if (!checkpoint(Stage::BodyRead)) return fail(Code::IsolationLost);
  if (!Read(api, stream, 0x14, report.consumed_bytes) ||
      !Read(api, stream, 0x1c, report.stream_aux_1c) ||
      report.consumed_bytes != static_cast<std::int32_t>(report.payload_bytes))
    return fail(Code::StreamInvalid);
  Graph destination;
  report.restored_tick = api.tick(owned.destination);
  if (!Inspect(api, owned.destination, destination) || !(destination.ready & 1) ||
      destination.state != source.state || report.restored_tick != report.source_tick)
    return fail(Code::RestoredGraphInvalid);
  report.restored_manager_state = destination.manager_state;
  if (!Distinct(source, destination)) return fail(Code::MutableAlias);
  std::uint64_t restored_semantic = 0;
  api.snapshot(owned.destination, &owned.verify);
  std::uint32_t restored_bytes = 0;
  if (!SnapshotDigest(api, owned.verify, restored_bytes, report.restored_digest) ||
      restored_bytes != report.payload_bytes || report.restored_digest != report.source_digest ||
      !api.semantic_digest(owned.destination, &restored_semantic) || restored_semantic != source_semantic)
    return fail(Code::StateMismatch);
  if (!checkpoint(Stage::Compared)) return fail(Code::IsolationLost);
  owned.DropPacket(owned.verify); owned.DropPacket(owned.packet); owned.DropManager(owned.source);
  report.source_destroyed = true;
  if (!checkpoint(Stage::SourceDestroyed)) return fail(Code::IsolationLost);
  Graph surviving;
  if (!Inspect(api, owned.destination, surviving) ||
      !api.semantic_digest(owned.destination, &restored_semantic) || restored_semantic != source_semantic)
    return fail(Code::SourceReleaseMismatch);
  for (std::uint32_t step = 0; step < request.steps_after_source_release; ++step) {
    if (!api.isolated()) return fail(Code::IsolationLost);
    api.original_step(owned.destination);
    if (api.tick(owned.destination) != report.source_tick + static_cast<std::int32_t>(step) + 1 ||
        !Inspect(api, owned.destination, surviving)) return fail(Code::StepMismatch);
  }
  if (request.exercise_commands && !api.exercise_commands(owned.destination)) return fail(Code::CommandMismatch);
  report.final_tick = api.tick(owned.destination);
  if (!checkpoint(Stage::Stepped)) return fail(Code::IsolationLost);
  if (!checkpoint(Stage::Cleanup)) return fail(Code::IsolationLost);
  owned.Cleanup(); report.cleaned_up = true;
  report.stage = Stage::Finished; report.code = Code::Passed;
  if (api.progress) api.progress(report);
  return report;
}
} // namespace firstlight::cold_clone
#else
namespace firstlight::cold_clone {
Report Run(const Api&, const Request&) { return {}; }
}
#endif
