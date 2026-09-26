constexpr const char* kLocalDeckPath = "/data/data/" FIRSTLIGHT_PACKAGE_NAME "/files/firstlight-decks-v1.bin";
void* g_local_deck_storage = nullptr;
std::uint64_t g_local_deck_digest = 0;
bool g_local_decks_restoring = false;

std::uint64_t local_deck_digest(const std::uint8_t* data, std::size_t size) {
  std::uint64_t value = 1469598103934665603ULL;
  for (std::size_t i=0; i<size; ++i) { value ^= data[i]; value *= 1099511628211ULL; }
  return value;
}

bool load_local_decks(void* storage) {
  FILE* file = std::fopen(kLocalDeckPath,"rb");
  if (!file) return false;
  std::uint64_t header[3]{};
  if (std::fread(header,1,sizeof(header),file)!=sizeof(header) || header[0]!=0x3144434c46313535ULL ||
      header[1]<16 || header[1]>2097152) { std::fclose(file); LOGE("local-decks invalid file header"); return false; }
  auto data = static_cast<std::uint8_t*>(std::malloc(header[1]));
  const bool valid = data && std::fread(data,1,header[1],file)==header[1] && std::fgetc(file)==EOF &&
      local_deck_digest(data,header[1])==header[2];
  std::fclose(file);
  if (!valid) { std::free(data); LOGE("local-decks invalid checksum"); return false; }
  void* stream = g_game_state_new(0x80);
  reinterpret_cast<void(*)(void*,const void*,int)>(g_libg_base+0x1211f58)(stream,data,static_cast<int>(header[1]));
  std::free(data);
  reinterpret_cast<void(*)(void*,void*)>(g_libg_base+0xfc37cc)(storage,stream);
  const int consumed = read_object_field<int>(stream,0x14);
  reinterpret_cast<void(*)(void*)>(g_libg_base+0x1212088)(stream); g_game_state_delete(stream);
  LOGI("local-decks restored bytes=%llu consumed=%d digest=%llu",static_cast<unsigned long long>(header[1]),consumed,
       static_cast<unsigned long long>(header[2]));
  g_local_deck_digest = header[2];
  g_local_decks_restoring = consumed == static_cast<int>(header[1]);
  return g_local_decks_restoring;
}

void save_local_decks() {
  if (!g_local_deck_storage) return;
  void* stream = g_game_state_new(0x80);
  reinterpret_cast<void(*)(void*,int)>(g_libg_base+0x1211ef4)(stream,4096);
  reinterpret_cast<void(*)(void*,void*)>(g_libg_base+0xfc34d4)(g_local_deck_storage,stream);
  const int size = read_object_field<int>(stream,0x14);
  auto data = read_object_field<const std::uint8_t*>(stream,0x30);
  if (size>=16 && size<=2097152 && process_range_is_readable(data,size)) {
    const std::uint64_t digest = local_deck_digest(data,size);
    if (digest != g_local_deck_digest) {
      constexpr const char* temporary = "/data/data/" FIRSTLIGHT_PACKAGE_NAME "/files/firstlight-decks-v1.tmp";
      FILE* file = std::fopen(temporary,"wb");
      const std::uint64_t header[3] = {0x3144434c46313535ULL,static_cast<std::uint64_t>(size),digest};
      bool ok = file && std::fwrite(header,1,sizeof(header),file)==sizeof(header) &&
          std::fwrite(data,1,size,file)==static_cast<std::size_t>(size) && std::fflush(file)==0 && fsync(fileno(file))==0;
      if (file) ok = std::fclose(file)==0 && ok;
      if (ok) ok = std::rename(temporary,kLocalDeckPath)==0;
      if (ok) g_local_deck_digest = digest;
      LOGI("local-decks saved=%d bytes=%d digest=%llu",ok,size,static_cast<unsigned long long>(digest));
    }
  }
  reinterpret_cast<void(*)(void*)>(g_libg_base+0x1212088)(stream); g_game_state_delete(stream);
}
