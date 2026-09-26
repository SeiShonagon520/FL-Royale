// Restrict the stock Home pager to the two complete offline surfaces.
using LocalPageSelect=void(*)(void*,int);
LocalPageSelect g_local_page_select=nullptr,g_local_page_position=nullptr;
using LocalBillingAvailable=bool(*)(void*);
LocalBillingAvailable g_local_billing_available=nullptr;
using LocalCardMaximum=int(*)(void*);
LocalCardMaximum g_local_card_maximum=nullptr;
using LocalSupportDefaults=void(*)(void*,void*);
LocalSupportDefaults g_local_support_defaults=nullptr;
void local_support_defaults(void* storage,void* card) {
  if(g_local_decks_restoring && storage==g_local_deck_storage) {
    LOGI("local-decks retained restored tower selections during Home initialization");
    return;
  }
  g_local_support_defaults(storage,card);
}
using LocalCardInfoRefresh=void(*)(void*,int);
LocalCardInfoRefresh g_local_card_info_refresh=nullptr;
using LocalEmoteClick=void(*)(void*,void*);
LocalEmoteClick g_local_emote_click=nullptr;
LocalEmoteClick g_local_support_click=nullptr;
void local_support_click(void* view,void* button) {
  if(g_local_deck_storage) {
    void* group=read_object_field<void*>(view,0x298);
    if(button==read_object_field<void*>(view,0x268) || (group &&
        (button==read_object_field<void*>(group,0x10) || button==read_object_field<void*>(group,0x20)))) {
      LOGI("local-home blocked tower troop upgrade");
      return;
    }
  }
  g_local_support_click(view,button);
}
void local_emote_click(void* view,void* button) {
  if(g_local_deck_storage && button==read_object_field<void*>(view,0x150)) {
    reinterpret_cast<void(*)(void*,void*)>(g_libg_base+0xc654a8)(
        read_object_field<void*>(view,0x170),read_object_field<void*>(view,0x160));
    // The local equip command refreshes synchronously and can remove this
    // animation before Use's click callback resumes. Keep native selection.
    auto clip=read_object_field<std::uint8_t*>(view,0x148);
    if(clip) {
      clip[8]=0;
      reinterpret_cast<void(*)(void*,int)>(g_libg_base+0x11c96ac)(clip,0);
    }
    static_cast<std::uint8_t*>(view)[0x178]=0;
    return;
  }
  g_local_emote_click(view,button);
}
void local_card_info_refresh(void* view,int tab) {
  g_local_card_info_refresh(view,tab);
  if(!g_offline_control_session_active.load()) return;
  // The stock card-info refresh owns these four buttons: level upgrade,
  // consumables, star-level upgrade and gem upgrade. Retain Select (+278).
  for(std::size_t offset:{0x280u,0x288u,0x290u,0x298u}) {
    auto button=read_object_field<std::uint8_t*>(view,offset);
    if(process_range_is_readable(button,9)) button[8]=0;
  }
}
int local_card_maximum(void* asset) {
  if(g_offline_control_session_active.load() && asset) {
    const int id=read_object_field<int>(asset,0x40);
    for(const auto& card:kLocalCards) if(card.id==id) return card.level;
    for(const auto& card:kLocalSupportCards) if(card.id==id) return card.level;
  }
  return g_local_card_maximum(asset);
}
bool local_billing_available(void* billing) {
  // Collection lazily asks this even with no store open. Cold offline boot
  // deliberately has no billing listener (billing+0x18).
  if(g_offline_control_session_active.load() && !read_object_field<void*>(billing,0x18)) return false;
  return g_local_billing_available(billing);
}
int local_home_page(void* pager,int index) {
  if(index==1 || index==2 || !g_local_deck_storage || !g_cold_scene_controller) return index;
  void* scene=read_object_field<void*>(g_cold_scene_controller,0x50);
  if(!process_range_is_readable(scene,0x318) || read_object_field<void*>(scene,0x310)!=pager) return index;
  static std::int64_t last_notice=0;
  const auto now=process_monotonic_now_ns();
  if(now-last_notice>1500000000LL) {
    last_notice=now;
    LocalJavaEnv java;
    if(java.env && g_local_runtime_class) {
      auto method=java.env->GetStaticMethodID(g_local_runtime_class,"showOfflineFeatureNotice","()V");
      if(method) java.env->CallStaticVoidMethod(g_local_runtime_class,method);
    }
  }
  LOGI("local-app unavailable Home page=%d",index);
  return 2;
}
void local_home_select(void* pager,int index) { g_local_page_select(pager,local_home_page(pager,index)); }
void local_home_position(void* pager,int index) { g_local_page_position(pager,local_home_page(pager,index)); }

// These listeners belong to the pinned libg build. Compare the actual button
// objects, rather than blocking screen coordinates or changing their artwork.
using LocalHomeClick=void(*)(void*,void*);
LocalHomeClick g_local_header_click=nullptr,g_local_menu_click=nullptr,g_local_settings_click=nullptr,g_local_arena_click=nullptr;
bool local_clicked_field(void* view,void* clicked,std::size_t offset) {
  return clicked && read_object_field<void*>(view,offset)==clicked;
}
void local_header_click(void* view,void* clicked) {
  if(g_offline_control_session_active.load()) {
    // King Road/Claim, player profile, news and social. Keep the menu and tabs.
    for(std::size_t offset:{0x210u,0x220u,0x228u,0x260u,0x290u}) {
      if(local_clicked_field(view,clicked,offset)) {
        LOGI("local-ui blocked header field=%zx",offset);
        return;
      }
    }
  }
  g_local_header_click(view,clicked);
}
void local_arena_click(void* view,void* clicked) {
  // The arena artwork and reward bubble dispatch this same button.
  if(g_offline_control_session_active.load() && local_clicked_field(view,clicked,0x1e0)) {
    LOGI("local-ui blocked arena");
    return;
  }
  g_local_arena_click(view,clicked);
}
void local_menu_click(void* view,void* clicked) {
  if(g_offline_control_session_active.load()) {
    // Account connection, TV Royale, leaderboards, tournaments, training, help.
    // Settings, Battle Log, Inbox and menu dismissal retain their own handlers.
    for(std::size_t offset:{0x210u,0x218u,0x220u,0x228u,0x238u,0x240u,0x258u}) {
      if(local_clicked_field(view,clicked,offset)) {
        LOGI("local-ui blocked menu field=%zx",offset);
        return;
      }
    }
  }
  g_local_menu_click(view,clicked);
}
void local_settings_click(void* view,void* clicked) {
  if(!g_offline_control_session_active.load() || local_clicked_field(view,clicked,0x148)) {
    g_local_settings_click(view,clicked);
    return;
  }
  if(local_clicked_field(view,clicked,0x278)) {
    LocalJavaEnv java;
    if(java.env && g_local_runtime_class) {
      auto method=java.env->GetStaticMethodID(g_local_runtime_class,"showRenamePlayer","()V");
      if(method) java.env->CallStaticVoidMethod(g_local_runtime_class,method);
      if(java.env->ExceptionCheck()) {
        java.env->ExceptionClear();
        LOGE("local-ui rename dialog unavailable");
      }
    }
    return;
  }
  LOGI("local-ui blocked settings");
}
void install_local_home_navigation(std::uintptr_t base) {
  static constexpr std::uint8_t arena_click[16]={0xff,0xc3,0x02,0xd1,0xe9,0x23,0x06,0x6d,0xfd,0x7b,0x07,0xa9,0xf7,0x43,0x00,0xf9};
  static constexpr std::uint8_t header_settings_click[16]={0xff,0x03,0x02,0xd1,0xfd,0x7b,0x05,0xa9,0xf6,0x57,0x06,0xa9,0xf4,0x4f,0x07,0xa9};
  static constexpr std::uint8_t menu_click[16]={0xff,0xc3,0x01,0xd1,0xfd,0x7b,0x04,0xa9,0xf5,0x2b,0x00,0xf9,0xf4,0x4f,0x06,0xa9};
  install_inline_hook(base,0xc0b16c,header_settings_click,reinterpret_cast<void*>(&local_header_click),reinterpret_cast<void**>(&g_local_header_click),"local Home header clicks");
  install_inline_hook(base,0x89112c,menu_click,reinterpret_cast<void*>(&local_menu_click),reinterpret_cast<void**>(&g_local_menu_click),"local Home menu clicks");
  install_inline_hook(base,0xa3c6dc,header_settings_click,reinterpret_cast<void*>(&local_settings_click),reinterpret_cast<void**>(&g_local_settings_click),"local Settings clicks");
  install_inline_hook(base,0xabacf8,arena_click,reinterpret_cast<void*>(&local_arena_click),reinterpret_cast<void**>(&g_local_arena_click),"local Home arena clicks");
  static constexpr std::uint8_t card_info[16]={0xff,0x03,0x03,0xd1,0xfd,0x7b,0x06,0xa9,0xfc,0x6f,0x07,0xa9,0xfa,0x67,0x08,0xa9};
  install_inline_hook(base,0xa4b22c,card_info,reinterpret_cast<void*>(&local_card_info_refresh),reinterpret_cast<void**>(&g_local_card_info_refresh),"local card upgrade controls");
  static constexpr std::uint8_t emote_click[16]={0xfd,0x7b,0xbd,0xa9,0xf5,0x0b,0x00,0xf9,0xf4,0x4f,0x02,0xa9,0xfd,0x03,0x00,0x91};
  install_inline_hook(base,0xbffff0,emote_click,reinterpret_cast<void*>(&local_emote_click),reinterpret_cast<void**>(&g_local_emote_click),"local emote equip refresh");
  static constexpr std::uint8_t support_click[16]={0xff,0x03,0x02,0xd1,0xfd,0x7b,0x04,0xa9,0xf7,0x2b,0x00,0xf9,0xf6,0x57,0x06,0xa9};
  install_inline_hook(base,0xa54c6c,support_click,reinterpret_cast<void*>(&local_support_click),reinterpret_cast<void**>(&g_local_support_click),"local tower troop upgrade controls");
  static constexpr std::uint8_t support_defaults[16]={0xff,0x83,0x01,0xd1,0xfd,0x7b,0x02,0xa9,0xf7,0x1b,0x00,0xf9,0xf6,0x57,0x04,0xa9};
  install_inline_hook(base,0xfc64d4,support_defaults,reinterpret_cast<void*>(&local_support_defaults),reinterpret_cast<void**>(&g_local_support_defaults),"local restored tower selections");
  static constexpr std::uint8_t maximum[16]={0x00,0x50,0x40,0xf9,0x08,0x00,0x40,0xf9,0x01,0x71,0x40,0xf9,0x20,0x00,0x1f,0xd6};
  install_inline_hook(base,0xe2b67c,maximum,reinterpret_cast<void*>(&local_card_maximum),reinterpret_cast<void**>(&g_local_card_maximum),"local level 11 upgrade cap");
  static constexpr std::uint8_t billing[16]={0xfd,0x7b,0xbe,0xa9,0xf3,0x0b,0x00,0xf9,0xfd,0x03,0x00,0x91,0xf3,0x03,0x00,0xaa};
  install_inline_hook(base,0x796c54,billing,reinterpret_cast<void*>(&local_billing_available),reinterpret_cast<void**>(&g_local_billing_available),"local billing availability");
  static constexpr std::uint8_t select[16]={0xff,0x03,0x01,0xd1,0xfd,0x7b,0x02,0xa9,0xf4,0x4f,0x03,0xa9,0xfd,0x83,0x00,0x91};
  static constexpr std::uint8_t position[16]={0xfd,0x7b,0xbe,0xa9,0xf4,0x4f,0x01,0xa9,0xfd,0x03,0x00,0x91,0xf3,0x03,0x01,0x2a};
  install_inline_hook(base,0x995b6c,select,reinterpret_cast<void*>(&local_home_select),reinterpret_cast<void**>(&g_local_page_select),"local Home page selection");
  install_inline_hook(base,0x9961e8,position,reinterpret_cast<void*>(&local_home_position),reinterpret_cast<void**>(&g_local_page_position),"local Home page position");
}
