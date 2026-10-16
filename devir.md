# Devir ve Mimari Dokümanı — Phase 3 Memory

## 1. Genel Durum
Bu dal (`phase3-memory`), SPEC.md ve DESIGN tavsiyelerinde (P0-07 ve P1-02/P1-03 ilk dilimi) belirtilen kalıcı yapılandırılmış bellek (durable structured memory) mimarisini eksiksiz olarak uygular.

Önceki aşamalardan gelen `packages/tools`, `packages/infra`, `packages/models` ve `packages/gateway` paketlerinin ana işleyişi bozulmamış, gereksiz yeniden yazımlardan kaçınılmıştır.

## 2. Gerçekleştirilen Mimari Bileşenler (`packages/core`)

### 2.1 Mevcut SqliteMemory Geçmişinin Korunması
- **Dosya**: `packages/core/src/memory.ts`
- **İşlev**: `turns` tablosu, `state` anahtar-değer deposu, `/reset` komutu ile sohbet oturumu sıfırlama, oturum izolasyonu (`session`) ve `request_id` replay deduplication yetenekleri %100 geriye dönük uyumlu biçimde korunmuştur.
- `/reset` çalıştırıldığında sadece sohbet turn geçmişi temizlenir; kullanıcının kalıcı profil ve olgusal hafızası (durable memory) silinmez.

### 2.2 Yapılandırılmış Bellek Tabloları ve Veri Modeli
- **Tablolar**:
  - `memory_records`: Kalıcı bellek kayıtları.
    - `id`: Benzersiz kanıt kimliği (Evidence ID, örn: `mem_m8x2k1_a3f9c2d1`).
    - `scope`: Oturum / kanal kapsamı (Session / channel scope isolation).
    - `kind`: `'profile'` (kullanıcı tercih ve özellikleri), `'note'` (olay/toplantı notları), `'fact'` (güncellenebilir olgular).
    - `text`: Bellek metni.
    - `category`: İsteğe bağlı etiket / konu.
    - `source` & `source_ids`: Kanıt ve kaynak takibi (provenance, `requestId`, oturum).
    - `trust`: Güven seviyesi (`user_explicit`, `observed`, `model_inferred`, `external`).
    - `created_at`, `updated_at`: Zaman damgaları.
    - `valid_from`, `valid_to`: Geçerlilik aralıkları.
    - `supersedes_id`, `superseded_by_id`: Eski olguların yerini alma (supersession) bağlantıları.
    - `revision`: Pozitif revizyon numarası (1'den başlar).
    - `status`: `'active'`, `'superseded'`, `'deleted'`.
    - `deleted_at`: Silinme zaman damgası.
  - `memory_revisions`: Revizyon geçmişi tablosu (eski metin, güncelleme nedeni, kaynak ve revizyon numarası).
  - `memory_deletions`: Silinen kayıtlar için denetim tombstones günlüğü.
  - `memory_fts`: SQLite FTS5 sanal tablosu (`record_id`, `scope`, `kind`, `text`, `category`).

### 2.3 SQLite FTS5 Tabanlı Arama & Sıralama
- Doğal FTS5 BM25 alaka düzeyi puanlaması (`ORDER BY f.rank`).
- `sanitizeFts5Query`: Özel semboller, tırnaklar, operatörler ve noktalama işaretlerinin FTS5 sözdizimi hatası vermesini önleyen Unicode-destekli belirteç temizleyici.
- Türkçe karakterler (`İstanbul`, `Türkçe`, `çay` vb.) ve önek araması (`"Type"*`) sorunsuz desteklenir.
- Bir kayıt silindiğinde veya yerine yenisi geçtiğinde (supersession) FTS5 tablosundan kaldırılarak aktif aramalarda görünmemesi garanti edilir.
- İsteğe bağlı embedding arayüzü (`EmbeddingProvider`, `SqliteMemoryOptions`) hazır bırakılmış, varsayılan olarak kapalı tutulmuştur.

### 2.4 Ajan Bellek Araçları (`packages/core/src/memory-tools.ts`)
- `remember`: Yeni profil tercihi, not veya olgusal bellek kaydeder; kanıt kimliği (evidence ID) döner. `supersedesId` verilirse eski kaydı arşivler.
- `revise`: Var olan kaydı kanıt kimliğiyle günceller, revizyonu artırır, geçmişe yazar. İsteğe bağlı `expectedRevision` ile Optimistic Concurrency Control (CAS) sağlar.
- `forget`: Belirtilen kanıt kimliğine sahip kaydı aktif aramadan kaldırır, FTS5'ten siler ve silinme günlüğü (tombstone) oluşturur.
- `recall`: Oturuma izole edilmiş FTS5 araması yapar; kanıt kimlikleri, revizyon ve kategoriyle biçimlendirilmiş sonuç döner.

### 2.5 Bağlam Derleme & Oturum İzolasyonu (Context Assembly)
- **Dosyalar**: `packages/core/src/agent.ts`, `packages/core/src/session-context.ts`
- `activeSessionContext`: `AsyncLocalStorage` kullanılarak eşzamanlı oturumlarda ve asenkron araç yürütmelerinde yarış durumları (race condition) olmadan etkin oturum takip edilir.
- `assembleContext`:
  - Modele aktarılan bellekler ham metin olarak dökülmez (`not dumped raw`).
  - Açıkça etiketlenir (`## Active Memory Context`, `### Profile`, `### Relevant Facts & Notes`).
  - Her madde kanıt kimliği (`[Evidence: mem_...]`), revizyon ve kategori içerir.
  - Maksimum kayıt adedi (`maxEntries`, varsayılan 10) ve karakter sınırı (`maxChars`, varsayılan 2000) ile sıkı şekilde sınırlandırılır.

## 3. Doğrulama ve Testler
- **İzole Araç Zinciri**: Node v20.20.2 ve pnpm 9.15.9 kullanılmıştır.
- **Tip Kontrolü**: `pnpm typecheck` (`tsc -b && tsc -p tsconfig.tests.json`) sıfır hata ile geçti.
- **Birim & Entegrasyon Testleri**:
  - `tests/memory.test.ts` eklendi (10 kapsamlı test senaryosu).
  - Vitest: **10 test dosyasında 78 testin tamamı** yeşil geçti.
  - Infra Jest: **1 suite içinde 6 testin tamamı** yeşil geçti.
  - Toplam **84 test** eksiksiz ve yeşil olarak doğrulanmıştır (`pnpm check` exit code: 0).
- **Test Edilen Kritik Senaryolar**:
  1. Restart recall (diske yazılı SQLite veritabanı kapatılıp açıldıktan sonra eksiksiz hatırlama ve FTS5 araması).
  2. Supersession (eski olgunun yerini yenisinin alması, revizyon artışı, eski olgunun aktif aramadan düşmesi, tarihsel incelemede kalması).
  3. Scope isolation (farklı oturum ve kanalların birbirinin belleğini okuyamaması, güncelleyememesi, silememesi).
  4. Deletion removes from active retrieval (silinen kaydın aktif aramadan, get ve list metotlarından çıkması, tombstone kaydı).
  5. FTS5 arama temelleri (önek, BM25 sıralaması, Türkçe karakterler, sembol arındırma).
  6. Optimistic concurrency (CAS) ve revizyon geçmişi.
  7. Bounded & labeled context assembly (etiketli bölümler, kanıt kimlikleri, sınırlandırma).
  8. Ajan döngüsü araç kullanımı (`remember`, `revise`, `forget`, `recall`).
  9. Mevcut turn geçmişi ve `/reset` davranışı ile uyumluluk.
  10. `createMemoryTools` doğrudan çağrı ve şema doğrulaması.

## 4. Önceki Aşama Notları
- Bellek altyapısı `packages/core` içinde tamamen hazır ve `packages/gateway` tarafından doğrudan kullanılabilir durumdadır.
- AWS altyapısına henüz deploy yapılmamıştır (`packages/infra` kuralları korunmuştur).

## 5. Python Yeniden Yazımı (Step 0: İskelet / Scaffold) — `python` Dalı

### 5.1 Genel Mimari ve Kararlar
- YusufClaw sistemi TypeScript'ten Python 3.12 (`uv`) altyapısına taşınmaktadır.
- Mevcut TypeScript paketleri (`packages/`) ve vitest testleri (`tests/*.test.ts`) silinmemiş veya değiştirilmemiştir; davranış referansı olarak korunmaktadır.
- LangGraph durum makinesi (`StateGraph`), FastAPI web arayüzü, Pydantic-Settings yapılandırması ve Typer CLI aracı devreye alınmıştır.

### 5.2 Gerçekleştirilen Bileşenler
1. **Paket Yönetimi ve Araç Zinciri (`pyproject.toml`, `uv.lock`)**:
   - Python `>=3.12` hedefi.
   - Bağımlılıklar: `langgraph`, `langchain-core`, `fastapi`, `uvicorn`, `pydantic-settings`, `boto3`, `httpx`, `openai`, `typer`, `redis`, `opentelemetry-api`, `opentelemetry-sdk`.
   - Geliştirme araçları: `pytest`, `pytest-asyncio`, `ruff`, `mypy`, `types-redis`.
2. **Yapılandırma (`src/yusufclaw/config.py`)**:
   - Pydantic-settings tabanlı strongly-typed yapılandırma; `.env` ve ortam değişkenlerini otomatik okur.
   - `model_provider`, `aws_profile`, `aws_region`, `bedrock_model_id`, `ollama_*`, `openai_*`, `redis_url`, `database_url` vb.
3. **Model Sağlayıcı Katmanı (`src/yusufclaw/models/`)**:
   - `ModelProvider` soyut arayüzü (`types.py`).
   - `FakeProvider`: Testler ve offline CLI çalıştırmaları için deterministik yanıt üretici.
   - `OllamaProvider`: Local `/api/chat` HTTP istemcisi.
   - `OpenAIProvider`: OpenAI Chat Completions uyumlu istemci.
   - `BedrockProvider`: AWS Bedrock Converse API (`boto3`) entegrasyonu; profile ve region yapılandırması; lazy istemci başlatımı (testlerde gerçek AWS çağrısı yapmaz).
   - `get_model_provider()`: Yapılandırmaya göre doğru sağlayıcıyı üreten fabrika.
4. **LangGraph Ajan Döngüsü (`src/yusufclaw/agent.py`)**:
   - `AgentState`: `messages` (`add_messages` reducer ile) ve `step_count`.
   - `model_node`: Mesajları normalize edip model sağlayıcısına iletir.
   - `tool_node`: Modelin talep ettiği araç çağrılarını asenkron olarak çalıştırır.
   - Koşullu kenar (`should_continue`): Araç çağrısı varsa ve `max_steps` aşılmadıysa `tool_node`'a, aksi takdirde `END`'e yönlendirir.
   - `Agent.run(prompt)` ve `Agent.run_state(prompt)` temiz arayüzleri.
5. **Alt Paketler ve Dokümantasyon (`src/yusufclaw/`)**:
   - `security/`: Deterministik politika motoru, taint tracking, onay kartları kapsam dokümantasyonu.
   - `memory/`: SQLite FTS5 ve pgvector kalıcı bellek mimarisi kapsam dokümantasyonu.
   - `tools/`: Sandbox shell, dosya, web fetch, tarayıcı, MCP entegrasyonu kapsam dokümantasyonu.
   - `skills/`: Markdown beceri yaşam döngüsü dokümantasyonu.
   - `routines/`: Cron tabanlı zamanlanmış görevler dokümantasyonu.
   - `telemetry/`: OpenTelemetry `setup_telemetry`, `get_tracer`, `get_meter` araçları.
   - `queue/`: Redis async istemcisi ve `RedisQueue` FIFO kuyruk soyutlaması.
   - `gateway/telegram.py`: Telegram Bot API adaptör taslağı ve dokümantasyonu.
6. **Web Gateway (`src/yusufclaw/gateway/web.py`)**:
   - FastAPI uygulaması; `GET /health` uç noktası (durum, versiyon, servis adı, model sağlayıcı).
7. **CLI Giriş Noktası (`src/yusufclaw/gateway/cli.py`)**:
   - Typer tabanlı `yusufclaw` komutu.
   - `yusufclaw turn "<mesaj>" --provider fake`: Tek turn ajan döngüsü çalıştırır ve yanıtı konsola basar.
   - `yusufclaw chat`: Etkileşimli veya argümanlı sohbet.
   - `yusufclaw version`: Versiyon bilgisi.
8. **Yerel Servisler (`docker-compose.dev.yml`)**:
   - `postgres`: `pgvector/pgvector:pg16` veritabanı (5432).
   - `redis`: `redis:7-alpine` mesaj kuyruğu (6379).
9. **Kılavuz Belgesi (`PYTHON-SCAFFOLD.md`)**:
   - Proje yerleşimi, mimari bileşenler ve çalıştırma komutlarının kapsamlı dökümü.

### 5.3 Test ve Doğrulama
- `uv run ruff check`: 0 hata, tam uyum.
- `uv run mypy src`: 0 hata, 20 kaynak dosyasında tam strict tip denetimi.
- `uv run pytest`: 23 testin tamamı offline, sahte sağlayıcılarla ve sıfır dış bağımlılıkla başarıyla geçmiştir.
- `uv run yusufclaw turn "Hello" --provider fake`: CLI üzerinden LangGraph döngüsü başarıyla test edilmiştir.

### 5.4 Sonraki Adımlar (Tamamlananlar: py-security)
- TypeScript testlerinden güvenlik kurallarını ve taint tracking mantığını Python'a taşımak (`src/yusufclaw/security/`) — [TAMAMLANDI].
- Araç katmanını (shell sandbox, files, fetch) taşımak (`src/yusufclaw/tools/`) — [TAMAMLANDI].
- Evals koşucusu ve 12 senaryoyu Python'a taşımak (`evals/`) — [TAMAMLANDI].

---

## 6. Python Güvenlik Alt Sistemi (Security Sprint Portu: YC-01, YC-02, YC-03, YC-05, YC-10) — `py-security` Dalı

### 6.1 Genel Mimari ve Kararlar
- TypeScript `phase3-security` dalında uygulanan güvenlik sprinti, Python 3.12, Pydantic v2 ve LangGraph mimarisiyle %100 uyumlu olarak `src/yusufclaw/security`, `src/yusufclaw/tools` ve `evals/` altına taşınmıştır.
- `packages/` (TypeScript) dizinine ve başka bir ajan tarafından üzerinde çalışılan `src/yusufclaw/memory` dizinine **kesinlikle dokunulmamıştır**.
- AWS altyapısına dokunulmamış; tüm testler ve değerlendirmeler tamamen offline olarak çalıştırılmıştır.

### 6.2 Gerçekleştirilen Bileşenler
1. **Pydantic Güvenlik Modelleri (`src/yusufclaw/security/models.py`)**:
   - `Label` (`origin`, `integrity: 'trusted' | 'untrusted'`, `secrecy: 'public' | 'private' | 'secret'`) ve `Value[T]`.
   - `SessionTaint` (`contaminated`, `sources`).
   - `ToolManifest` (`id`, `version`, `source`, `effects`, `resources`, `description_sha256`).
   - `ActionCard` ve `ActionCardRendered` (`approval_id`, `nonce`, `expires_at`, `tool_id`, `tool_version`, `args_sha256`, `rendered`).
   - `PolicyDecision` (`allow`, `deny`, `card`) ve `ConsumeResult`.
2. **Deterministik Politika Motoru (`src/yusufclaw/security/engine.py`)**:
   - `canonical_json`: RFC 8785 ve TypeScript implementasyonu ile birebir uyumlu sıralı, kompakt JSON serileştiricisi.
   - `Grants`: Monotonik yetki denetimi (`allowed_tools`, `allowed_hosts`, `allowed_paths`).
   - `render_card`: Gerçek araç argümanlarından deterministik insan-onay kartı üretimi.
   - `decide`: Monotonik Grants kontrolü -> Gizli etiketli verilerin `egress` ile çıkışının engellenmesi (FIDES) -> Kirlenmiş oturumda `egress`/`send_message`/`spend`/`exec` etkileri için zorunlu ActionCard (Willison Lethal Trifecta) -> Güvenilmeyen kaynaktan gelen hassas parametreler için ActionCard -> İzin (`allow`).
3. **ActionCard Yaşam Döngüsü ve Saklama (`src/yusufclaw/security/cards.py`)**:
   - `ActionCardStore`:
     - 16 baytlık kriptografik rastgele hex `nonce`.
     - 5 dakikalık TTL (`expires_at`).
     - Kanonik JSON SHA-256 argüman özeti doğrulaması (`args_sha256`).
     - Atomik tek seferlik tüketim (`consume`): Tekrar oynatma (*replay attack*) denemeleri anında reddedilir.
     - Argüman değişikliği engelleme: Tüketim sırasında sunulan argümanların özeti uyuşmazsa işlem reddedilir.
     - `deny`: Kartın reddedilmesi ve bir daha tüketilememesi.
4. **Telegram Onay Kartları ve Rıza Bütünlüğü (`src/yusufclaw/security/telegram.py`, `src/yusufclaw/gateway/telegram.py`)**:
   - `format_telegram_card_message`: Ajanın serbest metninden kesinlikle üretilmez (Consent Integrity / VAC); doğrudan kart verisinden formatlanır. Ajan iddiası yalnızca güvenilmeyen açıklama olarak ayrıştırılmış blokta sunulur.
   - `build_telegram_card_keyboard`: `approve:<id>:<nonce>`, `deny:<id>:<nonce>`, `details:<id>:<nonce>` inline buton verileri.
   - `handle_telegram_callback_data`: Kullanıcı allowlist, tekrar oynatma ve argüman sahteciliği denetimi.
5. **Güvenlik Korumalı Araçlar (`src/yusufclaw/tools/`)**:
   - `files.py`: `DEFAULT_WORKSPACE_DIR` (`.data/workspace`), `is_restricted_secret_path` (.env*, *.sqlite*, ~/.aws*, .git/config, id_rsa, id_ed25519), `resolve_safe_path` (symlink kaçış koruması, ata dizin doğrulaması, boş bayt engeli), `file_read`, `file_write` (onaylı), `file_list`.
   - `fetch.py`: `is_blocked_hostname`, `is_blocked_ip` (loopback, private RFC1918, link-local, CGNAT `100.64.0.0/10`, multicast, dokümantasyon IP'leri, IPv4-mapped IPv6, metadata IP'leri), `validate_host_ssrf` (DNS çözümlemesi sonrası IP denetimi), `web_fetch` (manuel redirect döngüsü ile her yönlendirme hedefinde SSRF doğrulaması).
   - `shell.py`: `contains_restricted_path` (`cat ../.env` ve traversal tespiti), `extract_command_binaries`, `is_dangerous_command`, `create_shell_tool` (allowlist, tehlikeli komut onayı, cwd sınırlaması, temiz ortam değişkenleri).
   - `time.py`: `current_time` salt-okur zaman aracı.
6. **LangGraph Ajan Döngüsü Güvenlik Entegrasyonu (`src/yusufclaw/agent.py`)**:
   - Oturum bazlı `SessionTaint` takibi (`get_taint`, `set_taint`).
   - Araç çağrısı öncesinde `decide()` politikası değerlendirmesi.
   - `read_untrusted` etkisi taşıyan araç çalıştığında oturumun kirlenmesi (`contaminated = True`).
   - `/reset` komutunda oturum turn ve taint durumunun sıfırlanması.
7. **Güvenlik Değerlendirme Koşucusu ve CI Fikstürleri (`evals/`, `scripts/run_evals.py`)**:
   - 12 adet YAML saldırı ve zararsız fikstürünün eksiksiz portu (`evals/fixtures/`).
   - `EvalRunner`: Sahte model sağlayıcı, izole geçici çalışma alanı ve mock ağ katmanı ile senaryoları çalıştıran deterministik değerlendirme motoru.
   - `scripts/run_evals.py`: Komut satırından ASR ve zararsız başarı oranını tablo halinde basan script.
   - `tests/python/test_evals.py`: Pytest entegrasyonu.

### 6.3 Test ve Doğrulama
- **Pytest**: **46 testin tamamı** yeşil geçti (`uv run pytest`, 3.82s).
- **Ruff**: 0 hata (`uv run ruff check` -> `All checks passed!`).
- **Mypy**: 0 hata (`uv run mypy src` -> `Success: no issues found in 29 source files`).
- **Güvenlik Değerlendirme Raporu (`uv run python scripts/run_evals.py`)**:
  - **Toplam Senaryo:** 12
  - **Saldırı Senaryosu:** 8
  - **Saldırı Başarı Oranı (ASR):** %0.00 (Hedef: %0.00, 8 saldırının 0'ı başarılı oldu).
  - **Zararsız Senaryo:** 4
  - **Zararsız Görev Başarı Oranı:** %100.00 (Hedef: %100.00).
  - **Üretilen ActionCard Sayısı:** 5
- **Dokunulmayan Alanlar:** `packages/` ve `src/yusufclaw/memory` %100 temiz ve dokunulmamıştır.

### 6.4 Sonraki Adımlar
- `py-memory` dalındaki SQLite FTS5 kalıcı bellek geliştirmeleri tamamlandığında bellek araçlarını `read_private` etkisiyle güvenlik döngüsüne bağlamak.
- Telegram gateway long-polling canlı döngüsünü bağlamak.
- Kılavuz belgesi: `PY-SECURITY.md`.
