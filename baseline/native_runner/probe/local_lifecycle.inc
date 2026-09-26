// A resumed online client requests a complete login/content reload. Local
// Home and battles already own their state, so retain them while the stock
// pause/resume path continues to manage graphics, sound and its pause flag.
using LocalReload=void(*)(void*);
LocalReload g_original_local_reload=nullptr;
void local_client_reload(void* app) {
  if(g_local_boot_ready.load(std::memory_order_acquire) && g_cold_scene_controller) {
    static_cast<std::uint8_t*>(app)[0x243]=0;
    LOGI("local-app retained scene on resume reload request");
    return;
  }
  g_original_local_reload(app);
}
void install_local_lifecycle(std::uintptr_t base) {
  static constexpr std::uint8_t prologue[16]={0xff,0x43,0x01,0xd1,0xfd,0x7b,0x01,0xa9,0xf7,0x13,0x00,0xf9,0xf6,0x57,0x03,0xa9};
  install_inline_hook(base,0x75a214,prologue,reinterpret_cast<void*>(&local_client_reload),reinterpret_cast<void**>(&g_original_local_reload),"local client lifecycle");
}
