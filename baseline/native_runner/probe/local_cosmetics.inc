// Owned collections and selections use the pinned engine's own storage codecs.
void* g_local_cosmetic_home=nullptr;
std::uint64_t g_local_cosmetic_digest=0;
constexpr const char* kLocalCosmeticPath="/data/data/" FIRSTLIGHT_PACKAGE_NAME "/files/firstlight-cosmetics-v1.bin";
constexpr LocalCardLevel kLocalSupportCards[]={{159000000,10,0},{159000001,5,0},{159000002,2,0},{159000004,2,0}};
struct LocalCosmeticCodec { std::size_t member; std::uintptr_t encode,decode; };
constexpr LocalCosmeticCodec kLocalCosmeticCodecs[]={{0x2f0,0xfb448c,0xfb46f0},{0x2f8,0x1155864,0x11559b0},{0x428,0xf81018,0xf811ec}};

std::string local_cosmetic_object_json(void* object,std::uintptr_t encode) {
  void* json=reinterpret_cast<void*(*)(void*)>(g_libg_base+encode)(object);
  // JSON::write takes StringBuilder (length includes NUL), not LogicString.
  struct NativeStringBuilder { int length=1,capacity=0; char* data=nullptr; } output;
  reinterpret_cast<void(*)(void*,void*)>(g_libg_base+0x12176e4)(json,&output);
  std::string result;
  if(output.data && output.length>=3 && output.length<=32768)
    result.assign(output.data,output.length-1);
  if(output.data) reinterpret_cast<void(*)(void*)>(g_libg_base+0x17e4b40)(output.data);
  release_json_object(json);
  return result.empty()?"{}":result;
}

std::string current_local_cosmetics_json() {
  if(!g_local_cosmetic_home) return "{}";
  void* emotes=read_object_field<void*>(g_local_cosmetic_home,0x2f0);
  void* skins=read_object_field<void*>(g_local_cosmetic_home,0x2f8);
  void* banners=read_object_field<void*>(g_local_cosmetic_home,0x428);
  void* layout=read_object_field<void*>(banners,0x10);
  // Use native JSON codecs: Long IDs and equipped emote slots have different
  // representations, and the skin's active selection is separate from its pool.
  return "{\"em\":"+local_cosmetic_object_json(emotes,0xfb51b0)+
      ",\"sk\":"+local_cosmetic_object_json(skins,0x1156130)+
      ",\"bn\":{\"bg\":"+std::to_string(read_object_field<int>(layout,8))+
      ",\"fg\":"+std::to_string(read_object_field<int>(layout,0xc))+"}}";
}

bool load_local_cosmetics(void* home) {
  g_local_cosmetic_digest=0;
  FILE* file=std::fopen(kLocalCosmeticPath,"rb");
  if(!file) return false;
  std::uint64_t header[3]{};
  if(std::fread(header,1,sizeof(header),file)!=sizeof(header) || header[0]!=0x31534f434c463135ULL ||
      header[1]<8 || header[1]>262144) { std::fclose(file); return false; }
  std::vector<std::uint8_t> data(header[1]);
  const bool valid=std::fread(data.data(),1,data.size(),file)==data.size() && std::fgetc(file)==EOF &&
      local_deck_digest(data.data(),data.size())==header[2];
  std::fclose(file);
  if(!valid) { LOGE("local-cosmetics invalid checksum"); return false; }
  void* stream=g_game_state_new(0x80);
  reinterpret_cast<void(*)(void*,const void*,int)>(g_libg_base+0x1211f58)(stream,data.data(),data.size());
  for(const auto& codec:kLocalCosmeticCodecs)
    reinterpret_cast<void(*)(void*,void*)>(g_libg_base+codec.decode)(read_object_field<void*>(home,codec.member),stream);
  const int consumed=read_object_field<int>(stream,0x14);
  reinterpret_cast<void(*)(void*)>(g_libg_base+0x1212088)(stream); g_game_state_delete(stream);
  const bool ok=consumed==static_cast<int>(data.size());
  if(ok) g_local_cosmetic_digest=header[2];
  LOGI("local-cosmetics restored=%d bytes=%zu consumed=%d",ok,data.size(),consumed);
  return ok;
}

void save_local_cosmetics() {
  if(!g_local_deck_storage || !g_local_cosmetic_home) return;
  void* stream=g_game_state_new(0x80);
  reinterpret_cast<void(*)(void*,int)>(g_libg_base+0x1211ef4)(stream,8192);
  for(const auto& codec:kLocalCosmeticCodecs)
    reinterpret_cast<void(*)(void*,void*)>(g_libg_base+codec.encode)(read_object_field<void*>(g_local_cosmetic_home,codec.member),stream);
  const int size=read_object_field<int>(stream,0x14);
  auto data=read_object_field<const std::uint8_t*>(stream,0x30);
  if(size>=8 && size<=262144 && process_range_is_readable(data,size)) {
    const auto digest=local_deck_digest(data,size);
    if(digest!=g_local_cosmetic_digest) {
      constexpr const char* temporary="/data/data/" FIRSTLIGHT_PACKAGE_NAME "/files/firstlight-cosmetics-v1.tmp";
      FILE* file=std::fopen(temporary,"wb");
      const std::uint64_t header[3]={0x31534f434c463135ULL,static_cast<std::uint64_t>(size),digest};
      bool ok=file && std::fwrite(header,1,sizeof(header),file)==sizeof(header) &&
          std::fwrite(data,1,size,file)==static_cast<std::size_t>(size) && std::fflush(file)==0 && fsync(fileno(file))==0;
      if(file) ok=std::fclose(file)==0 && ok;
      if(ok) ok=std::rename(temporary,kLocalCosmeticPath)==0;
      if(ok) g_local_cosmetic_digest=digest;
      LOGI("local-cosmetics saved=%d bytes=%d digest=%llu",ok,size,static_cast<unsigned long long>(digest));
    }
  }
  reinterpret_cast<void(*)(void*)>(g_libg_base+0x1212088)(stream); g_game_state_delete(stream);
}

void unlock_local_cosmetics(void* home,void* context) {
  const bool restored=load_local_cosmetics(home);
  void* emotes=read_object_field<void*>(home,0x2f0);
  void* catalog=read_object_field<void*>(reinterpret_cast<void*(*)(void*)>(g_libg_base+0xdd3348)(context),0);
  auto entries=read_object_field<void**>(catalog,0);
  const int count=read_object_field<int>(catalog,0xc);
  int available=0,defaults=0;
  for(int i=0;i<count && i<4096;++i) {
    void* entry=entries[i];
    // The Collection also lists two offer emotes whose Available flag is off.
    if(!read_object_field<std::uint8_t>(entry,0x99) && !read_object_field<std::uint8_t>(entry,0x9a)) continue;
    ++available;
    if(!reinterpret_cast<bool(*)(void*,void*)>(g_libg_base+0xfb4c98)(emotes,entry)) {
      // The owned vector owns cloned Long IDs. No reward/analytics callback.
      void* vector=static_cast<std::uint8_t*>(emotes)+0x40;
      const int used=read_object_field<int>(vector,0xc);
      if(used>=read_object_field<int>(vector,8))
        reinterpret_cast<void(*)(void*,int)>(g_libg_base+0xeec3f8)(vector,std::max(8,used*2));
      auto items=read_object_field<void**>(vector,0);
      items[used]=reinterpret_cast<void*(*)(void*)>(g_libg_base+0x121aa5c)(entry);
      const int next=used+1; std::memcpy(static_cast<std::uint8_t*>(vector)+0xc,&next,4);
    }
    if(!restored && defaults<8 && read_object_field<std::uint8_t>(entry,0x98)) {
      void* slot=read_object_field<void*>(emotes,0x50+defaults*8);
      std::memcpy(slot,entry,8); ++defaults;
    }
  }
  void* skins=read_object_field<void*>(home,0x2f8);
  auto skin_entries=read_object_field<void**>(skins,0x10);
  const int skin_count=read_object_field<int>(skins,0x1c);
  int collectable=0;
  for(int i=0;i<skin_count && i<4096;++i) {
    if(!skin_entries[i]) continue;
    void* item=static_cast<std::uint8_t*>(skin_entries[i])+0x68;
    auto vtable=read_object_field<void**>(item,0);
    if(!reinterpret_cast<bool(*)(void*)>(vtable[0x70/8])(item)) continue;
    const int id=reinterpret_cast<int(*)(void*)>(vtable[0x30/8])(item);
    reinterpret_cast<bool(*)(void*,int)>(g_libg_base+0x11563ec)(skins,id);
    ++collectable;
  }
  const int zero=0;
  std::memcpy(static_cast<std::uint8_t*>(skins)+0x3c,&zero,4); // no unread reward badges
  void* banners=read_object_field<void*>(home,0x428);
  void* banner_catalog=reinterpret_cast<void*(*)(void*)>(g_libg_base+0xdd3350)(context);
  const int banner_count=read_object_field<int>(banner_catalog,0xc);
  auto banner_entries=read_object_field<void**>(banner_catalog,0);
  int enabled=0;
  for(int i=0;i<banner_count && i<8192;++i) {
    void* entry=banner_entries[i];
    if(!read_object_field<std::uint8_t>(entry,0xc)) continue;
    reinterpret_cast<bool(*)(void*,void*)>(g_libg_base+0xf802e4)(banners,entry);
    ++enabled;
  }
  std::memcpy(static_cast<std::uint8_t*>(banners)+0x34,&zero,4);
  g_local_cosmetic_home=home;
  LOGI("local-cosmetics unlocked emotes=%d/%d skins=%d/%d banners=%d/%d",available,count,collectable,skin_count,enabled,banner_count);
}
