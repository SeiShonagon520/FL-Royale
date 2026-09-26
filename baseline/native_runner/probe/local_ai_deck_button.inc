// Reuse the native Decks page's dormant wizard button. Its parent owns layout,
// scrolling, modal input and lifetime just like the other deck editor buttons.
using LocalDeckEditorClick=void(*)(void*,void*);
LocalDeckEditorClick g_local_deck_editor_click=nullptr;

void local_ai_deck_button_refresh(void* view) {
  auto button=read_object_field<std::uint8_t*>(view,0x218);
  if(!process_range_is_readable(button,0x130)) return;
  button[8]=1;
  button[0x48]=1;
  void* clip=read_object_field<void*>(button,0x80);
  if(!process_range_is_readable(clip,0x58)) return;
  const auto count=read_object_field<std::uint16_t>(clip,0x4e);
  auto children=read_object_field<void**>(clip,0x50);
  if(count>64 || !process_range_is_readable(children,count*sizeof(void*))) return;
  LibgString label{};
  if(!initialize_libg_string("USE FOR AI",&label)) return;
  unsigned labels=0;
  for(unsigned i=0;i<count;++i) {
    void* child=children[i];
    auto vtable=read_object_field<void**>(child,0);
    if(!process_range_is_readable(vtable,0x90)) continue;
    // Same TextField predicate used by GameButton's native caption sizing.
    if(reinterpret_cast<bool(*)(void*)>(vtable[0x88/8])(child)) {
      reinterpret_cast<void(*)(void*,void*)>(g_libg_base+0x11f53b0)(child,&label);
      ++labels;
    }
  }
  LOGI("local-ai-deck button caption fields=%u",labels);
}

void local_ai_deck_button_click(void* view,void* clicked) {
  if(clicked && g_local_deck_storage && clicked==read_object_field<void*>(view,0x218)) {
    // Capture on the owning game thread, including a just-edited card or tower.
    const std::string deck=current_local_deck_json();
    LocalJavaEnv java;
    if(java.env && g_local_runtime_class) {
      auto method=java.env->GetStaticMethodID(g_local_runtime_class,"useCurrentDeckForAllAi","(Ljava/lang/String;)V");
      if(method) {
        jstring value=java.env->NewStringUTF(deck.c_str());
        if(value) {
          java.env->CallStaticVoidMethod(g_local_runtime_class,method,value);
          java.env->DeleteLocalRef(value);
        }
      }
    }
    LOGI("local-ai-deck clicked complete=%d",!deck.empty());
    return;
  }
  g_local_deck_editor_click(view,clicked);
}

void install_local_ai_deck_button(std::uintptr_t base) {
  static constexpr std::uint8_t refresh[16]={0x08,0x0c,0x41,0xf9,0xe8,0x03,0x00,0xb4,0xfd,0x7b,0xbe,0xa9,0xf4,0x4f,0x01,0xa9};
  // This replaces the online availability gate entirely; never execute its
  // trampoline, whose displaced instructions include a PC-relative branch.
  void* unused=nullptr;
  install_inline_hook(base,0xc6e9f8,refresh,reinterpret_cast<void*>(&local_ai_deck_button_refresh),&unused,"local AI deck button availability");
  static constexpr std::uint8_t click[16]={0xff,0x83,0x01,0xd1,0xfd,0x7b,0x04,0xa9,0xf4,0x4f,0x05,0xa9,0xfd,0x03,0x01,0x91};
  install_inline_hook(base,0xc6f7d8,click,reinterpret_cast<void*>(&local_ai_deck_button_click),
      reinterpret_cast<void**>(&g_local_deck_editor_click),"local AI deck button click");
}
