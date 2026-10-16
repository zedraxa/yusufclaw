# Devir ve Mimari Dokümanı — Phase 3 (Memory & Security)

## 1. Genel Durum
Bu dal, YusufClaw Phase 3 kapsamında hem kalıcı yapılandırılmış bellek mimarisini (`phase3-memory`: SQLite FTS5, supersession, provenance, session context) hem de P0 güvenlik sprintini (`phase3-security`: YC-01 çalışma alanı izolasyonu, YC-02 SSRF DNS/yönlendirme koruması, YC-03 ToolManifest & taint & Willison Lethal Trifecta politikası, YC-05 ActionCard ve Telegram inline onayları, YC-10 güvenlik evals paketi) birleştirir.
AWS altyapısına (`packages/infra`) dokunulmamış, geriye dönük uyumluluk korunmuştur.

---

## 2. Gerçekleştirilen Mimari ve Güvenlik Bileşenleri

### 2.1 Kalıcı Yapılandırılmış Bellek Mimarisi (`packages/core`)
- **Dosyalar:** `packages/core/src/memory.ts`, `packages/core/src/memory-tools.ts`, `packages/core/src/session-context.ts`
- **Tablolar:** `memory_records`, `memory_revisions`, `memory_deletions`, `memory_fts` (SQLite FTS5).
- **Bellek Türleri:** `profile` (kullanıcı tercihleri), `fact` (güncellenebilir olgular), `note` (olay notları).
- **Kanıt ve İzolasyon:** Her kayıtta `id` (`mem_...`), revizyon, provenance (`sourceIds`, `requestId`) ve oturum izolasyonu (`scope`).
- **Ajan Araçları:** `remember`, `revise` (CAS desteğiyle), `forget` (tombstone ile soft-delete), `recall` (BM25 sıralı FTS5 araması).
- **Bağlam Derleme (Context Assembly):** `activeSessionContext` (AsyncLocalStorage) ile eşzamanlı oturum takibi; etiketli, kanıt kimlikli, sınırlı (`maxEntries`, `maxChars`) sistem istemi enjeksiyonu.
- `/reset` komutu turn geçmişini ve oturum kirlenmesini temizler; durable profil/olgu belleğini korur.

### 2.2 Güvenlik Sprinti Modülleri (`packages/tools`, `packages/core`, `packages/gateway`, `evals/`)
- **YC-01 Çalışma Alanı İzolasyonu:**
  - `DEFAULT_WORKSPACE_DIR` `.data/workspace` dizinine alındı.
  - `isRestrictedSecretPath` / `containsRestrictedPath`: `.env*`, `*.sqlite`, `~/.aws`, `.git/config`, SSH anahtarları okuma ve komut kaçışlarına karşı kilitlendi.
- **YC-02 SSRF DNS ve Yönlendirme Koruması:**
  - `validateHostSSRF`: RFC1918, loopback, link-local, multicast, IPv4-mapped IPv6 ve bulut metadata IP/host doğrulaması (`dns.lookup({ all: true })`).
  - `web_fetch`: 5 sekmeli manuel redirect döngüsü ile her sekmede SSRF denetimi; `browser` için gezinme öncesi SSRF kontrolü.
- **YC-03 ToolManifest, Politika ve Oturum Kirlenmesi (Taint):**
  - `ToolManifest` arayüzü (`read_untrusted`, `exec`, `egress`, `spend` efektleri).
  - `policy.decide()` motoru: Lethal Trifecta engeli (kirlenmiş oturumda egress/exec/spend ActionCard onaysız çalışamaz), FIDES gizli veri sızıntı engeli ve yetki kısıtı (`Grants`).
  - `Agent.run` döngüsünde `read_untrusted` araçları oturumu kirlenmiş (`tainted`) işaretler.
- **YC-05 ActionCard ve Telegram Inline Onayları:**
  - `ActionCard` yapısı ve `ActionCardStore`: 16 bayt nonce, 5 dk TTL, argüman SHA-256 kanonik özeti, tek kullanımlık atomik tüketim (replay ve tampering engeli).
  - Deterministic kart şablonu (Consent Integrity, LLM serbest metninden üretilmez); Telegram inline butonları ve callback işleyicisi.
- **YC-10 Güvenlik Değerlendirme Paketi (`evals/`):**
  - Deterministik test koşucusu (`evals/runner.ts`) ve 12 YAML senaryosu (8 saldırı, 4 zararsız görev).
  - Attack Success Rate (ASR) %0.00, Benign Task Success Rate %100.00 hedefi.

---

## 3. Doğrulama ve Test Durumu
- **Node Sürümü:** Node 22 (`PATH=/home/yusuf/.nvm/versions/node/v22.22.3/bin:$PATH`).
- **Paket Derlemesi ve Tip Kontrolü:** `tsc -b && tsc -p tsconfig.tests.json` (0 hata).
- **Test Paketleri:** Vitest (core, tools, memory, gateway, evals) + AWS CDK Jest testi.
- **Güvenlik Evals:** `pnpm evals` ASR: %0.00.
- **AWS İzolasyonu:** `packages/infra` paketine dokunulmamıştır.

---

## 4. Sonraki Ajan İçin Notlar
1. Komutları çalıştırırken Node 22 yolunu önekleyin:
   ```bash
   export PATH="/home/yusuf/.nvm/versions/node/v22.22.3/bin:$PATH"
   ```
2. Bu birleştirme (`ts-final` etiketi), TypeScript sürümünün nihai kararlı referansıdır ve Python portu için davranış spesifikasyonu (behavior spec) teşkil eder.
3. `SECURITY-SPRINT.md` dosyası güvenlik sprintinin detaylı teknik raporudur.
