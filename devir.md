# Devir ve Mimari Dokümanı — Phase 2 Tools

## 1. Genel Durum
Bu dal (`phase2-tools`), SPEC.md'de tanımlanan Phase 2 araç setini (`packages/tools`) ve çekirdek döngü onay entegrasyonunu eksiksiz olarak uygular. Diğer daldaki (`phase2-models-web`) çalışma alanlarına (`packages/models`, `packages/infra`, `gateway` web chat) dokunulmamıştır.

## 2. Gerçekleştirilen Bileşenler (`packages/tools`)

### 2.1 Sandboxed Shell Tool (`shell`)
- **Dosya**: `packages/tools/src/shell.ts`
- **İşlev**: Komutları çalışma alanı dizini (`workspaceDir`) içinde çalıştırır.
- **İzin Listesi (Allowlist)**: `DEFAULT_ALLOWED_COMMANDS` listesi ile kısıtlıdır. İzin verilmeyen ikililer reddedilir.
- **Onay Kontrolü (`isDangerousCommand`)**:
  - Salt okunur güvenli komutlar (`ls`, `cat`, `head`, `tail`, `grep`, `pwd`, `git status`, `git diff`, `node -v` vb.) onaysız çalışır.
  - Yıkıcı ve değiştirici komutlar (`rm`, `mv`, `mkdir`, `kill`, `chmod`, `git commit`, `git reset`, `pnpm install`, çıktı yönlendirme `>` / `>>` vb.) kullanıcı onayı (`requiresApproval: true`) gerektirir.
- **Güvenlik & Sınırlar**: Dizin kaçışını (`cwd`) engeller, zaman aşımı (varsayılan 30s, maks 60s) ve çıktı sınırı (32.000 karakter) uygular. AbortSignal ile alt süreç ağacını sonlandırır.

### 2.2 Scoped File Tools (`file_read`, `file_write`, `file_list`)
- **Dosya**: `packages/tools/src/files.ts`
- **İşlev**: Dosya okuma, yazma ve listeleme işlemlerini yalnızca `workspaceRoot` sınırları içinde gerçekleştirir.
- **Güvenlik (`resolveSafePath`)**:
  - `../` göreli yol aşımı engellenir.
  - Çalışma alanı dışındaki mutlak yollar (`/etc/passwd` vb.) engellenir.
  - Null-byte (`\0`) saldırıları engellenir.
  - Çalışma alanı dışına işaret eden symlink'ler (hem mevcut dosyalar hem üst dizinler için) `realpathSync` ile kontrol edilerek engellenir.
- **Onay**:
  - `file_read`: Onay gerektirmez (`requiresApproval: false`), boyut sınırına (varsayılan 64KB, maks 1MB) tabidir.
  - `file_write`: Dosya sistemi değişikliği yaptığı için onay gerektirir (`requiresApproval: true`). Gerekli üst dizinleri güvenli izinlerle otomatik oluşturur.
  - `file_list`: Onay gerektirmez (`requiresApproval: false`), recursive destekler, `maxEntries` ile sınırlandırılır.

### 2.3 Web Fetch Tool (`web_fetch`)
- **Dosya**: `packages/tools/src/fetch.ts`
- **İşlev**: Güvenli HTTP/HTTPS istekleri atar.
- **Güvenlik**:
  - Sadece `http:` ve `https:` protokollerine izin verilir (`file:`, `ftp:` vb. engellenir).
  - SSRF Koruması: Bulut metadata IP'leri (`169.254.169.254`, `fd00:ec2::254`, `metadata.google.internal`, `100.100.100.200`) engellenir.
  - Akış Boyut Sınırı: Yanıt gövdesi stream reader ile okunarak `maxBytes` (varsayılan 100KB, maks 1MB) aşımında akış iptal edilir, bellek şişmesi önlenir.
  - Zaman aşımı ve iptal sinyali entegrasyonu mevcuttur.
  - Birim testleri için enjekte edilebilir `fetcher` arayüzü sunar.

### 2.4 Playwright Browser Tool (`browser`)
- **Dosya**: `packages/tools/src/browser.ts`
- **İşlev**: Sayfa başlığı, metni veya HTML içeriğini alma (`navigate`, `content`, `title`).
- **Opsiyonel Bağımlılık**: Playwright yüklü değilse çökmez; kullanıcıya kurulum komutunu (`pnpm add -D playwright && npx playwright install`) bildiren nazik bir açıklama döner.
- **Kaynak Temizliği**: `finally` bloklarında `page`, `context` ve `browser` örneklerinin kapatılması garanti edilir.
- **Test Edilebilirlik**: Pluggable `launcher` desteği sayesinde gerçek tarayıcı ikilisi olmadan tüm yaşam döngüsü test edilebilir.

### 2.5 Delegate to Local Agents Tool (`delegate_agent`)
- **Dosya**: `packages/tools/src/delegate.ts`
- **İşlev**: `agy -p <prompt>` veya `codex exec <prompt>` alt süreçlerini çalıştırır.
- **Güvenlik Kapısı**: `ENABLE_AGENT_DELEGATION=true` (veya `1`) ortam değişkeni tanımlı değilse devre dışıdır.
- **Onay**: Otonom kod çalıştırabilen yerel ajan çağrıları her zaman onay gerektirir (`requiresApproval: true`).
- **Sınırlar**: Çalışma dizini workspace içinde tutulur, zaman aşımı ve çıktı sınırları uygulanır.

### 2.6 Core Agent Loop & Approval Uyarlaması
- **Dosyalar**: `packages/tools/src/index.ts`, `packages/core/src/agent.ts`, `packages/gateway/src/main.ts`
- `AgentTool.requiresApproval` alanı `boolean | ((input: Record<string, unknown>) => boolean)` tipine genişletildi.
- Çekirdek ajan döngüsünde (`packages/core/src/agent.ts`), `entry.tool.requiresApproval` fonksiyon veya boolean olarak dinamik değerlendirilir.
- Böylece güvenli shell komutları kullanıcıyı yormadan akarken, yıkıcı shell komutları ve dosya yazma işlemleri CLI'da kullanıcıya `Type yes:` şeklinde sorulur, Telegram ve tek seferlik CLI'da ise güvenli şekilde reddedilir (`Tool approval denied`).
- `createDefaultTools()` fabrikası tüm Phase 2 araçlarını `current_time` ile birlikte döner ve `packages/gateway/src/main.ts` içinde ajana kaydedilmiştir.

## 3. Doğrulama ve Testler
- **İzole Araç Zinciri**: Node v20.20.2 (`/home/yusuf/.npm/_npx/4d903b83cb5f3f5a/node_modules/node/bin/node`) ve pnpm 9.15.9 kullanılmıştır. Sistem küresel kurulumuna dokunulmamıştır.
- **Test Kapsamı**: `tests/tools.test.ts` eklenmiş olup, 5 test dosyasında toplam **44 testin tamamı** eksiksiz ve yeşil olarak geçmektedir (`pnpm check` exit code: 0).

## 4. Sonraki Ajan İçin Notlar
- `phase2-models-web` dalı birleştirildiğinde `packages/tools` ve `packages/core` çakışmasız entegre olacaktır.
- `packages/models` veya `packages/infra` paketlerinde hiçbir değişiklik yapılmamıştır.
