// Parser-only experiment for the pinned v15 20963 message. This file is not
// included by the normal probe build. A deliberate _APP-only test build may
// include it immediately before game_app_pending_event_setter_hook() and call
// offline_login_synthetic_parse_once_on_error(event) from that hook.
//
// The body contains no captured account fields. It is the shortest mandatory
// prefix supported by the parser's EOF checks: two zero 8-byte identifiers,
// seven zero-length strings (big-endian uint32 lengths), and eight zero VInts.
// Parsing success would prove only syntax, not a usable Home or deck.

#include "offline_login_pointer_guard.h"

namespace offline_login_synthetic_probe {

constexpr std::uintptr_t kLoginMessageConstructorOffset = 0x0102e8d0;
constexpr std::uintptr_t kLoginMessageVtableOffset = 0x018a4c70;
constexpr std::uintptr_t kLoginMessageDecoderOffset = 0x0102ee18;
constexpr std::uintptr_t kLoginMessageDeletingDestructorOffset = 0x0102f9e8;
constexpr std::uintptr_t kLoginMessageIdGetterOffset = 0x0102f994;
constexpr std::uintptr_t kByteStreamAssignOffset = 0x01213450;
constexpr std::uintptr_t kArrayNewOffset = 0x017e4d60;
constexpr std::size_t kLoginMessageAllocationBytes = 0x1b0;
constexpr std::size_t kSyntheticBodyBytes = 52;
constexpr char kEnableMarker[] =
    "/data/user/0/" FIRSTLIGHT_PACKAGE_NAME "/files/offline_login_parse_once.flag";

std::atomic<bool> g_parse_attempted{false};
std::atomic<std::uint32_t> g_pending_event_calls{0};
std::atomic<std::uint32_t> g_frame_checks{0};
std::atomic<std::int32_t> g_last_sampled_login_state{-1000000};
std::atomic<std::uint32_t> g_state_events{0};

// Kept as a field-oriented builder so the structural assumption can be
// checked independently of the native parser. No production profile data.
void make_empty_20963_body(std::uint8_t (&body)[kSyntheticBodyBytes]) {
  std::memset(body, 0, sizeof(body));
}

// Unlike the live 20963 handler, parser-only testing never transfers these
// allocated prefix values into the account object. The generated message's
// deleting destructor releases its ByteStream and inline +0x150 string but
// not these parsed pointer fields, so release them before deleting it.
std::uint32_t release_unmoved_prefix_fields(void *message) {
  using ObjectMethod = void (*)(void *);
  auto free_object = reinterpret_cast<ObjectMethod>(g_libg_base + 0x017e4b50);
  auto destroy_string = reinterpret_cast<ObjectMethod>(g_libg_base + 0x0138a700);
  constexpr std::size_t kIdentifierOffsets[] = {0x90, 0x98};
  constexpr std::size_t kStringOffsets[] = {0xa0, 0xa8, 0xb0, 0xd0, 0xb8, 0xf0, 0xf8};
  std::uint32_t released = 0;
  for (std::size_t offset : kIdentifierOffsets) {
    void *const value = read_object_field<void *>(message, offset);
    if (!process_range_is_readable(value, 8)) {
      continue;
    }
    void *const cleared = nullptr;
    std::memcpy(static_cast<std::uint8_t *>(message) + offset, &cleared, sizeof(cleared));
    free_object(value);
    ++released;
  }
  for (std::size_t offset : kStringOffsets) {
    void *const value = read_object_field<void *>(message, offset);
    if (!process_range_is_readable(value, 0x10)) {
      continue;
    }
    void *const cleared = nullptr;
    std::memcpy(static_cast<std::uint8_t *>(message) + offset, &cleared, sizeof(cleared));
    destroy_string(value);
    free_object(value);
    ++released;
  }
  return released;
}

void log_offline_home_gate_preflight() {
  std::int32_t connection_state = -1;
  std::int32_t connection_index = -1;
  bool connection_slot_readable = false;
  void *const root_slot = reinterpret_cast<void *>(g_libg_base + kNormalStartLoginStateRootOffset);
  if (process_range_is_readable(root_slot, sizeof(void *))) {
    void *const root = read_object_field<void *>(root_slot, 0);
    if (process_range_is_readable(root, sizeof(void *))) {
      void *const connection = read_object_field<void *>(root, 0);
      if (process_range_is_readable(connection, 0x70)) {
        connection_state = read_object_field<std::int32_t>(connection, 8);
        connection_index = read_object_field<std::int32_t>(connection, 0x58);
        const std::uintptr_t begin = reinterpret_cast<std::uintptr_t>(
            read_object_field<void *>(connection, 0x60));
        const std::uintptr_t end = reinterpret_cast<std::uintptr_t>(
            read_object_field<void *>(connection, 0x68));
        // 0xadb3a8 initializes begin/end/cap and 0xadb4c8 grows the vector
        // to thirty-two 0x20-byte slots. Check the actual vector bounds.
        if (begin != 0 && end >= begin && end - begin <= 0x400 &&
            (end - begin) % 0x20 == 0 && connection_index >= 0 &&
            static_cast<std::uintptr_t>(connection_index) < (end - begin) / 0x20) {
          connection_slot_readable = process_range_is_readable(
              reinterpret_cast<void *>(begin + static_cast<std::uintptr_t>(connection_index) * 0x20), 0x20);
        }
      }
    }
  }
  void *const game_app_slot = reinterpret_cast<void *>(g_libg_base + 0x01997cf0);
  void *const game_app = process_range_is_readable(game_app_slot, sizeof(void *))
                             ? read_object_field<void *>(game_app_slot, 0)
                             : nullptr;
  const bool game_app_context = process_range_is_readable(game_app, 0x518) &&
                                read_object_field<void *>(game_app, 0x510) != nullptr;
  void *const global_a = offline_login_pointer_guard::read_live_object(
      g_libg_base, offline_login_pointer_guard::kGlobalAManagerGot, process_range_is_readable);
  const bool global_a_ready = process_range_is_readable(global_a, 0x41);
  void *const global_b = offline_login_pointer_guard::read_live_object(
      g_libg_base, offline_login_pointer_guard::kHomeWrapperGot, process_range_is_readable);
  const bool global_b_ready = process_range_is_readable(global_b, 0x20);
  void *const global_b_child = global_b_ready ? read_object_field<void *>(global_b, 0x18) : nullptr;
  void *const text_wrapper_slot = reinterpret_cast<void *>(g_libg_base + 0x018e59a8);
  void *const text_wrapper = process_range_is_readable(text_wrapper_slot, sizeof(void *))
                                 ? read_object_field<void *>(text_wrapper_slot, 0)
                                 : nullptr;
  void *const text_child = process_range_is_readable(text_wrapper, sizeof(void *))
                               ? read_object_field<void *>(text_wrapper, 0)
                               : nullptr;
  void *const login_flag_slot = reinterpret_cast<void *>(g_libg_base + 0x018e5df8);
  void *const login_flag = process_range_is_readable(login_flag_slot, sizeof(void *))
                               ? read_object_field<void *>(login_flag_slot, 0)
                               : nullptr;
  const bool login_flag_zero = process_range_is_readable(login_flag, 1) &&
                               read_object_field<std::uint8_t>(login_flag, 0) == 0;
  void *const dispatch_owner = offline_login_pointer_guard::read_live_object(
      g_libg_base, offline_login_pointer_guard::kDispatcherGot, process_range_is_readable);
  LOGI("offline-login parse-only preflight state=%d index=%d indexedSlotReadable=%d gameAppContext=%d "
       "globalAReady=%d globalBReady=%d globalBChildPresent=%d textChildReady=%d "
       "loginFlagZero=%d dispatchOwnerReadable=%d",
       connection_state, connection_index, connection_slot_readable ? 1 : 0, game_app_context ? 1 : 0,
       global_a_ready ? 1 : 0, global_b_ready ? 1 : 0, global_b_child != nullptr ? 1 : 0,
       process_range_is_readable(text_child, 0x68) ? 1 : 0, login_flag_zero ? 1 : 0,
       process_range_is_readable(dispatch_owner, sizeof(void *)) ? 1 : 0);
}

std::int32_t read_app_login_state() {
  void *const root_slot = reinterpret_cast<void *>(g_libg_base + kNormalStartLoginStateRootOffset);
  if (!process_range_is_readable(root_slot, sizeof(void *))) {
    return -1;
  }
  void *const root = read_object_field<void *>(root_slot, 0);
  if (!process_range_is_readable(root, sizeof(void *))) {
    return -1;
  }
  void *const connection = read_object_field<void *>(root, 0);
  return process_range_is_readable(connection, 0x10)
             ? read_object_field<std::int32_t>(connection, 8)
             : -1;
}

void parse_once() {
  if (g_libg_base == 0 || g_game_state_new == nullptr) {
    LOGE("offline-login parse-only skipped: libg/allocator unavailable");
    return;
  }
  log_offline_home_gate_preflight();

  using MessageConstructor = void (*)(void *);
  using ByteStreamAssign = void (*)(void *, void *, std::int32_t);
  using MessageMethod = void (*)(void *);
  using ArrayNew = void *(*)(std::size_t);

  auto *const expected_vtable = reinterpret_cast<void *>(g_libg_base + kLoginMessageVtableOffset);
  if (!process_range_is_readable(expected_vtable, 0x30)) {
    LOGE("offline-login parse-only skipped: vtable unavailable");
    return;
  }
  auto *const decode = read_object_field<void *>(expected_vtable, 0x18);
  auto *const destroy = read_object_field<void *>(expected_vtable, 8);
  auto *const id_getter = read_object_field<void *>(expected_vtable, 0x28);
  if (decode != reinterpret_cast<void *>(g_libg_base + kLoginMessageDecoderOffset) ||
      destroy != reinterpret_cast<void *>(g_libg_base + kLoginMessageDeletingDestructorOffset) ||
      id_getter != reinterpret_cast<void *>(g_libg_base + kLoginMessageIdGetterOffset)) {
    LOGE("offline-login parse-only skipped: vtable method mismatch decodeRva=0x%zx destroyRva=0x%zx "
         "idGetterRva=0x%zx",
         decode == nullptr ? static_cast<std::size_t>(0) :
                             static_cast<std::size_t>(reinterpret_cast<std::uintptr_t>(decode) - g_libg_base),
         destroy == nullptr ? static_cast<std::size_t>(0) :
                              static_cast<std::size_t>(reinterpret_cast<std::uintptr_t>(destroy) - g_libg_base),
         id_getter == nullptr ? static_cast<std::size_t>(0) :
                                static_cast<std::size_t>(reinterpret_cast<std::uintptr_t>(id_getter) - g_libg_base));
    return;
  }
  auto constructor = reinterpret_cast<MessageConstructor>(g_libg_base + kLoginMessageConstructorOffset);
  auto assign = reinterpret_cast<ByteStreamAssign>(g_libg_base + kByteStreamAssignOffset);
  auto new_array = reinterpret_cast<ArrayNew>(g_libg_base + kArrayNewOffset);
  void *message = g_game_state_new(kLoginMessageAllocationBytes);
  if (message == nullptr) {
    LOGE("offline-login parse-only skipped: message allocation failed");
    return;
  }
  constructor(message);

  std::uint8_t body[kSyntheticBodyBytes] = {};
  make_empty_20963_body(body);
  // ByteStream and its base destructor use operator new[] / delete[] for this
  // owned buffer; do not allocate it with the message's scalar operator new.
  void *owned_body = new_array(sizeof(body));
  if (owned_body == nullptr) {
    LOGE("offline-login parse-only skipped: body allocation failed");
    reinterpret_cast<MessageMethod>(destroy)(message);
    return;
  }
  std::memcpy(owned_body, body, sizeof(body));

  // 0x102e8d0 calls 0x121bcdc with w1=1, which initializes message+0x88.
  // Keep that constructor default for parser-only testing; the online wire
  // version has not been captured and must be observed before any injection.
  const std::int32_t version = read_object_field<std::int32_t>(message, 0x88);
  assign(static_cast<std::uint8_t *>(message) + 8, owned_body, sizeof(body));
  reinterpret_cast<MessageMethod>(decode)(message);
  auto *const stream = static_cast<std::uint8_t *>(message) + 8;
  const std::int32_t consumed = read_object_field<std::int32_t>(stream, 0x14);
  const std::int32_t length = read_object_field<std::int32_t>(stream, 0x18);
  const bool identities_allocated = read_object_field<void *>(message, 0x90) != nullptr &&
                                    read_object_field<void *>(message, 0x98) != nullptr;
  LOGI("offline-login parse-only id=20963 version=%d consumed=%d length=%d identitiesAllocated=%d noDispatch=1",
       version, consumed, length, identities_allocated ? 1 : 0);
  const std::uint32_t released = release_unmoved_prefix_fields(message);
  LOGI("offline-login parse-only prefix allocations released=%u stream-owned-body-released-by-destructor=1", released);
  reinterpret_cast<MessageMethod>(destroy)(message);
}

}  // namespace offline_login_synthetic_probe

void offline_login_synthetic_parse_once_on_error(std::int32_t event) {
  const std::uint32_t call = offline_login_synthetic_probe::g_pending_event_calls.fetch_add(
                                 1, std::memory_order_relaxed) + 1;
  const int marker_result = access(offline_login_synthetic_probe::kEnableMarker, F_OK);
  const int marker_errno = marker_result == 0 ? 0 : errno;
  if (call <= 8) {
    LOGI("offline-login pending-event call=%u event=%d markerVisible=%d markerErrno=%d",
         call, event, marker_result == 0 ? 1 : 0, marker_errno);
  }
  if (event != 2 && event != 3) {
    return;
  }
  if (marker_result != 0 ||
      offline_login_synthetic_probe::g_parse_attempted.exchange(true, std::memory_order_acq_rel)) {
    return;
  }
  offline_login_synthetic_probe::parse_once();
}

// The stock 96% connection dialog need not come through GameApp::pendingEvent.
// This bounded fallback validates only the message parser on the render thread
// while the same explicit app-private marker is present. It never dispatches.
void offline_login_synthetic_parse_once_on_frame() {
  if (g_libg_base == 0 || g_game_state_new == nullptr) {
    return;
  }
  const std::uint32_t check = offline_login_synthetic_probe::g_frame_checks.fetch_add(
                                  1, std::memory_order_relaxed) + 1;
  if (check > 1800) {
    return;
  }
  const int marker_result = access(offline_login_synthetic_probe::kEnableMarker, F_OK);
  const int marker_errno = marker_result == 0 ? 0 : errno;
  if (check <= 3) {
    LOGI("offline-login frame-check=%u markerVisible=%d markerErrno=%d",
         check, marker_result == 0 ? 1 : 0, marker_errno);
  }
  if (marker_result != 0) {
    return;
  }
  const std::int32_t state = offline_login_synthetic_probe::read_app_login_state();
  const std::int32_t previous = offline_login_synthetic_probe::g_last_sampled_login_state.exchange(
                                    state, std::memory_order_acq_rel);
  if ((state != previous || check % 120 == 0) &&
      offline_login_synthetic_probe::g_state_events.fetch_add(1, std::memory_order_relaxed) < 24) {
    LOGI("offline-login state-timeline frame=%u appLoginState=%d changed=%d",
         check, state, state == previous ? 0 : 1);
    if ((state == 1 || state == 3) && previous != state) {
      offline_login_synthetic_probe::log_offline_home_gate_preflight();
    }
  }
  if (check > 600 ||
      offline_login_synthetic_probe::g_parse_attempted.exchange(true, std::memory_order_acq_rel)) {
    return;
  }
  offline_login_synthetic_probe::parse_once();
}
