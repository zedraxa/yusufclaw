# Devir ve Mimari Dokümanı — Phase 3 Security Sprint

## 1. Genel Durum
Bu dal (`phase3-security`), `docs/RESEARCH-ASIST.md` (§1.3 ve Yol Haritası Tablosu) içerisinde belirlenen P0 güvenlik açıklarını ve tehdit modellerini kapatan Güvenlik Sprinti'ni (YC-01, YC-02, YC-03, YC-05, YC-10) eksiksiz olarak uygular. 

AWS altyapısına (`packages/infra`) kesinlikle dokunulmamış, tüm mevcut testler korunmuş ve her madde ayrı commit'lerle kaydedilmiştir.

---

## 2. Gerçekleştirilen Güvenlik Modülleri

### 2.1 YC-01: Çalışma Alanı İzolasyonu ve Kısıtlı Yollar
- **Commit:** `8c30597`
- **Dosyalar:** `packages/tools/src/files.ts`, `packages/tools/src/shell.ts`, `packages/tools/src/index.ts`
- **İşlev:**
  - `DEFAULT_WORKSPACE_DIR` deposu kökünden `.data/workspace` dizinine taşındı.
  - `isRestrictedSecretPath` ile `.env*`, `*.sqlite`, `~/.aws`, `.git/config`, SSH anahtarları için mutlak okuma reddi konuldu.
  - Shell aracında `containsRestrictedPath` ile argümanlardaki kaçışlar (`cat ../.env`, `cat ~/.aws/credentials`) engellendi.

### 2.2 YC-02: SSRF DNS ve Yönlendirme Doğrulaması
- **Commit:** `ab4324c`
- **Dosyalar:** `packages/tools/src/fetch.ts`, `packages/tools/src/browser.ts`
- **İşlev:**
  - RFC1918, loopback, link-local, multicast, IPv4-mapped IPv6 adresleri ve bulut metadata IP/alan adları engellendi.
  - `validateHostSSRF` hem doğrudan IP'leri hem de DNS çözümlemesi (`dns.lookup({ all: true })`) sonucundaki adresleri denetler.
  - `web_fetch` için 5 sekmeli manuel redirect döngüsü kuruldu; her sekmede SSRF kontrolü tekrarlanır.
  - `browser` (Playwright) aracına istek öncesi SSRF doğrulaması eklendi.

### 2.3 YC-03: ToolManifest, Deterministik Politika ve Oturum Kirlenmesi
- **Commit:** `faceedc`
- **Dosyalar:** `packages/tools/src/manifest.ts`, `packages/core/src/policy/`, `packages/core/src/agent.ts`
- **İşlev:**
  - `ToolManifest` arayüzü ile araç efektleri (`read_untrusted`, `exec`, `egress`, `spend`, vb.) ve kaynak bilgileri tiplendirildi.
  - `policy.decide()` motoru ile Willison Lethal Trifecta engeli konuldu: Kirlenmiş oturumda `egress`, `send_message`, `spend`, `exec` eylemleri kullanıcı onaysız (ActionCard'sız) çalışamaz.
  - `Agent.run` döngüsüne `taints` oturum hafızası eklendi; `read_untrusted` araçları oturumu kirlenmiş (`contaminated`) yapar.

### 2.4 YC-05: ActionCard ve Telegram Inline Onay Sistemi
- **Commit:** `5750706`
- **Dosyalar:** `packages/core/src/policy/cards.ts`, `packages/gateway/src/telegram.ts`
- **İşlev:**
  - `ActionCard` yapısı ve `ActionCardStore` uygulandı (16 bayt kriptografik nonce, 5 dk TTL, kanonik JSON argüman SHA-256 özeti, tek seferlik atomik tüketim).
  - Tekrar oynatma (*replay*) ve onay sonrası argüman değişikliği (*argument tampering*) reddedilir.
  - Telegram adapter'ına inline klavye butonları (`approve`, `deny`, `details`) ve `callback_query` işleyicisi eklendi.
  - **Consent Integrity:** Onay kartı metni kesinlikle LLM serbest metninden üretilmez; yalnızca deterministik kart şablonundan oluşturulur.

### 2.5 YC-10: Güvenlik Değerlendirme Paketi (`evals/`) ve CI
- **Commit:** `84dd704`
- **Dosyalar:** `evals/types.ts`, `evals/runner.ts`, `evals/fixtures/*.yaml`, `tests/evals.test.ts`
- **İşlev:**
  - YAML formatında 12 senaryo (8 saldırı, 4 zararsız görev) içeren deterministik test koşucusu geliştirildi.
  - Egress isteklerini yakalayan test proxy mekanizması kuruldu.
  - **Metrikler:**
    - Attack Success Rate (ASR): **%0.00** (0 / 8 saldırı başarılı)
    - Benign Task Success Rate: **%100.00** (4 / 4 zararsız görev tamamlandı)
    - ActionCards Generated: **5** (Onay yükü raporlandı)
  - `pnpm evals` ve `pnpm check` komutlarına entegre edildi.

---

## 3. Test ve Bütünlük Durumu

- **Node Sürümü:** Node >= 20.19 (Ortamda: `/home/yusuf/.nvm/versions/node/v22.22.3/bin/node`).
- **Paket Derlemesi (`tsc -b`):** 0 hata.
- **Tip Kontrolü (`tsc -p tsconfig.tests.json`):** 0 hata.
- **Vitest Test Paketi:** 10 dosya, 83 testin tamamı geçti (`PASS`).
- **AWS CDK Jest Paketi:** 1 dosya, 6 testin tamamı geçti (`PASS`).
- **Toplam Test:** 89 test eksiksiz geçti.
- **AWS İzolasyonu:** `packages/infra` paketine dokunulmamıştır.

---

## 4. Sonraki Ajan İçin Notlar

1. Node komutlarını çalıştırırken Node 22 yolunu önekleyin:
   ```bash
   PATH="/home/yusuf/.nvm/versions/node/v22.22.3/bin:$PATH" pnpm check
   ```
2. Güvenlik sprinti tamamlandığı için `docs/RESEARCH-ASIST.md` tablosundaki bir sonraki aşamalar:
   - **YC-04:** Shell için sandbox entegrasyonu (bubblewrap / podman).
   - **YC-06:** Olay günlüğü (`runs` ve `run_events` tabloları).
   - **YC-07 / YC-08:** Kalıcı bellek şeması ve `/forget` komutu.
3. `SECURITY-SPRINT.md` dosyası bu sprintin eksiksiz teknik referansını içerir.
