#pragma once

#include <cstddef>
#include <cstdint>

namespace firstlight::cold_clone {

// Deliberately has no address resolver, hook installer, socket, JNI registration,
// startup initializer, controller setter, or command-injection entry point.
enum class Code : std::uint32_t {
  Disabled, Running, AlreadyAttempted, InvalidApi, IsolationLost, AllocationFailed,
  SourceInvalid, SnapshotInvalid, DestinationNotCold, StreamInvalid,
  RestoredGraphInvalid, MutableAlias, StateMismatch, SourceReleaseMismatch,
  StepMismatch, Passed, CommandMismatch
};
enum class Stage : std::uint32_t {
  Admission, SourceConstructed, SourceLoaded, SnapshotWritten,
  DestinationConstructed, HeaderRead, BodyRead, Compared, SourceDestroyed,
  Stepped, Cleanup, Finished
};

struct Report {
  Code code = Code::Disabled;
  Stage stage = Stage::Admission;
  std::int32_t source_tick = -1;
  std::int32_t restored_tick = -1;
  std::int32_t final_tick = -1;
  std::int32_t source_manager_state = -1;
  std::int32_t restored_manager_state = -1;
  std::int32_t world_state = -1;
  std::uint32_t payload_bytes = 0;
  std::int32_t consumed_bytes = -1;
  std::int32_t stream_aux_1c = -1; // Raw diagnostic word; not called an error flag.
  std::uint64_t source_digest = 0;
  std::uint64_t restored_digest = 0;
  bool source_destroyed = false;
  bool cleaned_up = false;
};

struct Api {
  // Must verify pinned libg/resources, initialized assets/tilemap, isolated
  // disposable process, exclusive owner thread, no registered scene/worker,
  // no online B/profile, and an external watchdog deadline. Must not mutate.
  bool (*isolated)() = nullptr;
  bool (*copy_readable)(const void*, void*, std::size_t) = nullptr;
  void* (*allocate)(std::size_t) = nullptr;                         // 17e4b20
  void (*construct)(void*, std::int32_t, void*, void*, std::int32_t) = nullptr; // 1060ba8
  void (*setup)(void*, std::int32_t) = nullptr;                    // 1060f84
  void (*original_load)(void*, void*, void*, void*) = nullptr;     // 1061b1c original
  void (*destroy)(void*) = nullptr;                               // 10610b0
  void (*destroy_base)(void*) = nullptr;                          // 988844
  void (*deallocate)(void*) = nullptr;                            // 17e4b50
  // Supply adapter around cr_call_state_snapshot(manager,&out,ad9a50).
  // This is NOT the ABI of ad9a50 itself, which returns through x8.
  void (*snapshot)(void*, void**) = nullptr;
  void (*delete_snapshot)(void*) = nullptr;                       // 105e8bc
  void (*flip_read)(void*) = nullptr;                             // 121221c
  void (*rewind_read)(void*) = nullptr;                           // 121220c
  std::int32_t (*read_byte)(void*) = nullptr;                     // ByteStream v+150
  std::int32_t (*read_boolean)(void*) = nullptr;                  // ByteStream v+e0
  void (*read_header)(void*, void*) = nullptr;                    // 10634c0
  void (*read_body)(void*, void*, std::int32_t, void*) = nullptr; // 106379c
  std::int32_t (*tick)(void*) = nullptr;                          // 1062bc0
  void (*original_step)(void*) = nullptr;                        // 10620a4 original
  // Required independent semantic check: six native towers/HP, both own decks
  // and current hand identities/forms and elixir, synthetic player identities.
  // Return deterministic value without addresses or hidden state from another
  // episode. No mutation or global probe-manager registration is allowed.
  bool (*semantic_digest)(void*, std::uint64_t*) = nullptr;
  // Optional later phase, exercised only after cold reconstruction and source
  // destruction have independently passed in a fresh process.
  bool (*exercise_commands)(void*) = nullptr;
  // Optional scalar-only progress checkpoint for watchdog diagnostics.
  void (*progress)(const Report&) = nullptr;
};

struct Request {
  bool enabled = false;
  void* context_a = nullptr;
  void* context_b = nullptr;
  // Caller owns parsed fresh synthetic JSON until Run returns. Root prepares
  // its location tilemap before admission. No account/network payloads.
  void* parsed_match_json = nullptr;
  // First run should leave this zero. Subsequent process may test <=20 steps.
  std::uint32_t steps_after_source_release = 0;
  bool exercise_commands = false;
};

// Also requires compilation with FIRSTLIGHT_ENABLE_COLD_CLONE_DIAGNOSTIC=1.
// At most one admitted attempt per process. No native fault recovery: watchdog
// terminates the disposable process on crash/hang. Never invoke in live VM3.
Report Run(const Api&, const Request&);

}  // namespace firstlight::cold_clone
