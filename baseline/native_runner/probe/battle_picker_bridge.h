#pragma once

#include <jni.h>
#include <cstdint>

namespace firstlight {

struct ReadyBattleRequest {
  std::uint64_t request_id = 0;
  std::int64_t model_generation = 0;
  char ai_id[49] = {};
};

// Call during crprobe JNI_OnLoad, while the game class loader is active.
// Missing Java route classes leave the bridge disabled; legacy APKs still boot.
bool InitBattlePickerBridge(JavaVM *vm, JNIEnv *env);

// Call only for a dynamically verified main lobby Battle-button click while
// the offline local-match route is armed. No libg state is accessed here.
// A true result means the Android picker owns this click.
bool RequestBattlePickerFromGameThread();

// Call on the game's own thread. The Java/UI or policy worker callbacks only
// write this mailbox and must never manipulate native game objects directly.
bool TakeReadyBattleRequest(ReadyBattleRequest *out);

// Use when the native lobby/match generation changes before a handoff.
void ForgetBattlePickerRequest();

}  // namespace firstlight
