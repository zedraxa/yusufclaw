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

### 5.4 Sonraki Adımlar
- TypeScript testlerinden güvenlik kurallarını ve taint tracking mantığını Python'a taşımak (`src/yusufclaw/security/`) [TAMAMLANDI — bkz. bölüm 7].
- `packages/core/src/memory.ts` içindeki SQLite FTS5 yapılandırılmış bellek mantığını Python'a taşımak (`src/yusufclaw/memory/`) [TAMAMLANDI — bkz. bölüm 6].
- Araç katmanını (shell sandbox, files, fetch) taşımak (`src/yusufclaw/tools/`) [TAMAMLANDI — bkz. bölüm 7].
- Telegram botu ve web AG-UI event stream mekanizmasını bağlamak (`py-gateway`).

---

## 6. Python Bellek Alt Sistemi Taşıması (`py-memory` Dalı)

### 6.1 Tamamlanan Mimari ve Modüller
1. **Veri Modelleri (`src/yusufclaw/memory/models.py`)**:
   - Pydantic v2 tabanlı `MemoryRecord`, `RememberInput`, `ReviseInput`, `SearchOptions`, `ListOptions`, `ContextAssemblyOptions`, `MemoryRevisionRecord`, `MemoryDeletionRecord`, `EmbeddingProvider`.
   - `AliasChoices` ve özellik (property) eşleyicileriyle hem Pythonic `snake_case` hem de TypeScript uyumlu `camelCase` (örneğin `source_ids` / `sourceIds`, `supersedes_id` / `supersedesId`) desteklendi.
2. **Oturum İzolasyonu (`src/yusufclaw/memory/context.py`)**:
   - `AsyncLocalStorage` karşılığı olarak Python `contextvars.ContextVar` ve `session_scope` context manager'ı devreye alındı. Eşzamanlı asenkron çağrılarda yarış durumu olmadan `(session, request_id)` takibi sağlandı.
3. **FTS ve Metin Arama (`src/yusufclaw/memory/fts.py`)**:
   - `sanitize_fts5_query`: Unicode destekli (`\w+`) belirteç temizleyici; Türkçe karakterleri (`İstanbul`, `Türkçe`, `Kadıköyde`), önek aramalarını (`"Type"*`) ve FTS5 operatör arındırmasını eksiksiz gerçekleştirdi.
   - `sanitize_tsquery`: PostgreSQL tam metin arama belirteç temizleyicisi.
4. **SQLite FTS5 Bellek Deposu (`src/yusufclaw/memory/sqlite.py`)**:
   - Harici servis gerektirmeyen, WAL modunda çalışan offline `SqliteMemory` motoru.
   - `turns`, `state`, `memory_records`, `memory_revisions`, `memory_deletions` ve `memory_fts` (FTS5 BM25) tabloları.
   - `remember`, `revise` (CAS destekli), `supersede`, `forget` (tombstone audit), `get`, `search`, `list`, `get_profile`, `assemble_context` ve `/reset` / `history` metotları.
5. **PostgreSQL + pgvector Asenkron Bellek Deposu (`src/yusufclaw/memory/postgres.py`)**:
   - SQLAlchemy 2.0 `AsyncEngine` ve `psycopg` async sürücüsü.
   - Declarative modeller: `TurnModel`, `StateModel`, `MemoryRecordModel`, `MemoryRevisionModel`, `MemoryDeletionModel`.
   - `pgvector.sqlalchemy.Vector` 1536-boyutlu gömme vektörleri ve kosinüs mesafesi ile `search_vector`.
   - PostgreSQL `to_tsvector` ve `plainto_tsquery` tam metin araması.
   - Docker olmaksızın testlerin offline koşabilmesi için `sqlite+aiosqlite` ve bellek içi kosinüs mesafesi geri düşüş (fallback) desteği.
6. **Ajan Bellek Araçları (`src/yusufclaw/memory/tools.py`)**:
   - `remember`, `revise`, `forget`, `recall` araçları `MemoryTool` / `ToolDefinition` arayüzü ile LangGraph ve ajan döngüsüne sunuldu.
7. **LangGraph Ajan Entegrasyonu (`src/yusufclaw/agent.py`)**:
   - `Agent` bellek nesnesiyle başlatıldığında sistem istemine etiketli ve sınırlı bellek bağlamını (`assemble_context`) otomatik ekler.
   - Araç yürütmesinde bellek araçlarını otomatik çağırır.
   - `Agent.handle(session, request_id, text)` ile oturum kapsamı, girdi doğrulama (1-16000 karakter), `/reset` ve `/help` komut kesicileri ve turn geçmişi kaydı sağlandı.

### 6.2 Test ve Doğrulama
- **Port Edilen Testler (`tests/python/test_memory.py`)**:
  - TypeScript `tests/memory.test.ts` içindeki 10 senaryonun tamamı pytest'e 1:1 uyarlandı.
  - PostgreSQL async motor işlemleri, pgvector vektör araması ve Pydantic model doğrulama testleri eklendi (toplam 13 bellek testi).
- **Sonuçlar**:
  - `uv run pytest`: 36 testin tamamı offline başarıyla geçti (0 hata).
  - `uv run ruff check`: 0 uyarı/hata.
  - `uv run mypy src tests/python`: 33 kaynak dosyasında strict tip denetimi 0 hata ile geçti.
  - `packages/` (TypeScript), `src/yusufclaw/security/`, `src/yusufclaw/tools/` ve AWS yapılandırmalarına dokunulmadı.

## 7. Python Güvenlik Alt Sistemi (Security Sprint Portu: YC-01, YC-02, YC-03, YC-05, YC-10) — `py-security` Dalı

### 7.1 Genel Mimari ve Kararlar
- TypeScript `phase3-security` dalında uygulanan güvenlik sprinti, Python 3.12, Pydantic v2 ve LangGraph mimarisiyle %100 uyumlu olarak `src/yusufclaw/security`, `src/yusufclaw/tools` ve `evals/` altına taşınmıştır.
- `packages/` (TypeScript) dizinine ve başka bir ajan tarafından üzerinde çalışılan `src/yusufclaw/memory` dizinine **kesinlikle dokunulmamıştır**.
- AWS altyapısına dokunulmamış; tüm testler ve değerlendirmeler tamamen offline olarak çalıştırılmıştır.

### 7.2 Gerçekleştirilen Bileşenler
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

### 7.3 Test ve Doğrulama
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

### 7.4 Sonraki Adımlar
- Bellek araçlarını `read_private` etkisiyle güvenlik döngüsüne bağlamak (memory + security birleşik `agent.py` hazır; etki etiketleri sıkılaştırılabilir).
- Telegram gateway long-polling canlı döngüsünü bağlamak.
- Kılavuz belgesi: `PY-SECURITY.md`.

---

## 8. Python Gateway Alt Sistemi Taşıması (`py-gateway` Dalı)

### 8.1 Tamamlanan Mimari ve Modüller
1. **Asenkron Olay Yolu (`src/yusufclaw/gateway/bus.py`)**:
   - `IncomingMessage`: Strongly-typed model, camelCase (`userId`, `conversationId`, `requestId`) ve snake_case uyumu.
   - `MessageBus`: Kanal bazlı allowlist denetimi (`cli`, `telegram`, `web`). Yetkisiz erişimlerde `UnauthorizedUserError` fırlatır.
   - İzole oturum anahtarı: `json.dumps([channel, userId, conversationId], separators=(",", ":"))`.
   - Çağrıyı `Agent.handle`'a onay geri çağrısı (`approve`) ile iletir.
2. **Onay Sağlayıcı Protokolü ve Eylem Kartı Biçimlendirmesi (`src/yusufclaw/gateway/approval.py`)**:
   - `ApprovalProvider` protokolü: `register`, `consume`, `deny`, `get` metotları ile YC-05 onay yaşam döngüsü.
   - `ActionCard` ve `ActionCardRendered` modelleri: Değiştirilemez başlık, kesin komut, etkiler, taint durumu ve geri alınabilirlik.
   - `format_telegram_card_message`: Eylem kartını Türkçeleştirilmiş formatta (`⚠️ [ONAY GEREKİYOR...]`) oluşturur; ajanın güvenilmeyen iddialarını (`[Ajanın İddiası (Güvenilmeyen Açıklama)]`) açıkça ayırır.
   - `build_telegram_card_keyboard`: Doğrulanabilir callback query verileri (`approve:<id>:<nonce>`, `deny:...`, `details:...`) içeren inline klavye üretir.
3. **Telegram Bot Ağ Geçidi (`src/yusufclaw/gateway/telegram.py`)**:
   - `TelegramApi`: `https://api.telegram.org` REST istemcisi. Ağ hatalarında token gizler (`RuntimeError("Telegram network request failed")`). 429 durumlarında `retry_after` sürelerine uyar.
   - `split_telegram_text`: Mesajları 4000 karakteri aşmayacak şekilde, UTF-16 surrogate veya çok baytlı emoji kod noktalarını parçalamadan böler.
   - `TelegramAdapter`:
     - Grup sohbetlerini, bot mesajlarını, metinsiz güncellemeleri ve yetkisiz kullanıcıları filtreler.
     - Bellek durumunda (`telegram-offset:<sha256(token)>`) güncelleme ofsetini kalıcı tutar ve yeniden başlatmada kaldığı yerden devam eder.
     - Terminal çakışmalarda (401, 403, 409) derhal durur; geçici hatalarda yeniden dener.
     - İç sağlayıcı hatalarını ve gizli anahtarları kullanıcı çıktısından arındırır.
     - `ApprovalProvider` üzerinden interaktif eylem kartı onaylarını yönetir (replay ve argüman tahrifatı koruması).
     - Webhook modu için `handle_webhook()` desteği sunar.
4. **FastAPI Web Ağ Geçidi ve AG-UI SSE Akışı (`src/yusufclaw/gateway/web.py`, `web_page.py`)**:
   - Minimal web UI: `GET /` (HTML), `GET /app.js` (JavaScript), `GET /style.css` (CSS). Token içermeyen güvenli HTML, sıkı CSP (`frame-ancestors 'none'`) ve güvenli DOM (`textContent`, sıfır `innerHTML`/`localStorage`).
   - Sabit zamanlı Bearer token denetimi (`WEB_AUTH_TOKEN`, SHA-256 digest).
   - Origin doğrulaması: Cross-origin istekler engellenir (403).
   - İçerik boyutu (<=100,000 bayt, 413) ve strict `application/json` tipi (415) denetimi.
   - `POST /api/chat`: Standart turn çalıştırma; sağlayıcı hatalarını 502'ye maskeleme.
   - `POST /api/chat/stream` & `GET /api/events`: AG-UI 1.0 uyumlu Server-Sent Events (SSE) akışı (`progress`, `message`, `done`).
   - `GET /api/routines` & `WebAdapter.deliver`: Zamanlanmış görev çıktılarını kullanıcı gelen kutusunda tekilleştirerek sunma.
   - `WebAdapter`: Ephemeral port desteği (`port: 0`), Uvicorn asenkron sunucu yönetimi ve temiz kapatma.
5. **CLI Ağ Geçidi (`src/yusufclaw/gateway/cli.py`)**:
   - `run_cli`: `--message` / `-m` ile one-shot çalıştırma; argümansız TTY dışı çalıştırmada hata fırlatma; interaktif terminal döngüsü (`You> `, `/help`, `/exit`, eylem kartı onayı).
   - Typer uygulaması (`cli_app`): `turn`, `chat`, `version` ve doğrudan `--message` çağrıları.
6. **Yapılandırma ve Ana Giriş Noktası (`src/yusufclaw/gateway/config.py`, `main.py`)**:
   - Pydantic tabanlı `GatewayConfig` ve `load_config(env)`: Bedrock, OpenAI, Ollama ve Telegram yapılandırma kurallarını sıkı doğrular.
   - `main.py`: `cli`, `web`, `telegram` modlarını dinamik başlatır; SIGINT/SIGTERM sinyalleri ile zarif kapatma uygular.

### 8.2 Test ve Doğrulama
- **Port Edilen Testler**:
  - `tests/python/test_bus.py`: Olay yolu yetkilendirme ve oturum formatlama.
  - `tests/python/test_gateway_config.py`: Yapılandırma doğrulama kuralları.
  - `tests/python/test_telegram_gateway.py`: Telegram filtreleme, ofset kalıcılığı, 409 yönetimi, emoji bölme, hata arındırma, YC-05 ActionCard formatı ve inline onay callback query korumaları.
  - `tests/python/test_web_gateway.py`: Bearer auth, cross-origin engelleme, CSP, token-free HTML/JS, 502 hata maskeleme, inbox teslimatı, ephemeral port yaşam döngüsü, AG-UI SSE akışı.
  - `tests/python/test_cli_gateway.py`: One-shot mod, TTY denetimi, interaktif terminal döngüsü ve onay komutları.
- **Sonuçlar**:
  - `uv run pytest`: **60 testin tamamı** offline, sahte sağlayıcılarla ve sıfır dış ağ bağlantısıyla yeşil geçti (5.36s).
  - `uv run ruff check .`: 0 hata, 0 uyarı.
  - `uv run mypy src tests/python`: 43 kaynak dosyasında strict tip denetimi 0 hata ile geçti.
  - `packages/` (TypeScript), `src/yusufclaw/security/`, `src/yusufclaw/tools/`, `src/yusufclaw/memory/` ve AWS yapılandırmalarına dokunulmadı.

---

## 9. Python Skills ve Routines Alt Sistemi — `py-skills` Dalı

### 9.1 Genel Mimari ve Kararlar
- TypeScript referansındaki `packages/core/src/skills.ts`, `packages/core/src/routines.ts`, `packages/gateway/src/routines.ts` ve `tests/skills-routines.test.ts` birebir Python 3.12, asyncio ve LangGraph uyumlu olarak `src/yusufclaw/skills` ve `src/yusufclaw/routines` altına taşınmıştır.
- `src/yusufclaw/gateway`, `security`, `tools`, `memory` ve `packages/` dizinlerine kesinlikle dokunulmamıştır (Gateway başka bir işçi tarafından yönetilmektedir).
- Testlerin ve çalışma ortamının tamamı offline çalışacak şekilde kurgulanmıştır (sıfır ağ, sıfır AWS).

### 9.2 Gerçekleştirilen Bileşenler
1. **Güvenilir Yerel Beceri Yükleyicisi (`src/yusufclaw/skills/loader.py`, `src/yusufclaw/skills/__init__.py`)**:
   - `load_skills` (async), `load_skills_sync` (sync) ve `loadSkills` takma adı.
   - Salt yerel Markdown (`.md`) dosyaları okunur; betikler (`.js`, `.py`, `.sh`), include yapıları ve frontmatter kod çalıştırmaları reddedilir.
   - Symlink izleme engellenmiştir (`is_symlink()` kontrolü).
   - Gizli dosyalar (`.*`) ve `README.md` atlanır.
   - Deterministik alfabetik sıralama garantisi.
   - Sıkı derinlik ve boyut sınırları:
     - `MAX_DEPTH = 8`
     - `MAX_FILE_BYTES = 32_768` (32 KB tek dosya sınırı)
     - `MAX_TOTAL_BYTES = 131_072` (128 KB kümülatif sınır)
   - Prompt enjeksiyonu TS ile birebir uyumlu: `Trusted local skill instructions:\n\n### Skill: {name}\n{content}`.
   - `Agent.__init__` içinde `skills: str | None = None` parametresi ve model çağrısında sistem promptu ile birleştirilerek enjeksiyonu (`[system_prompt, skills, memory_context]`).
2. **5-Alanlı UTC Cron Ayrıştırıcısı (`src/yusufclaw/routines/cron.py`)**:
   - `parse_cron` ve `parseCron` takma adı.
   - Dakika, saat, ayın günü, ay, haftanın günü (0 ve 7 = Pazar).
   - Liste (`0,15`), aralık (`9-17`), adım (`*/15`, `1-5/2`) desteği.
   - Standart cron DOM/DOW "OR" semantiği.
3. **Routines JSON Yapılandırma Yükleyicisi (`src/yusufclaw/routines/loader.py`)**:
   - `load_routines`, `load_routines_sync` ve `loadRoutines` takma adı.
   - En fazla 100 rutin kontrolü.
   - Regex `^[a-zA-Z0-9_-]{1,64}$` ile ID doğrulama ve teklik denetimi.
   - İzin verilen kanallar: `cli`, `telegram`, `web`.
   - İstem uzunluğu <= 16.000 karakter.
   - Hata durumunda konfigürasyon ve gizli içerikleri sızdırmadan deterministik `ValueError("Invalid routines configuration")` fırlatılır.
4. **Kanal Koşucusu ve Protokoller (`src/yusufclaw/routines/runner.py`)**:
   - `RoutineTransport` (`dispatch`, `deliver`) ve `RoutineRunner` (`run_routine`) dar Protokolleri.
   - `ChannelRunner` & `ChannelRunnerOptions`: Gateway koduna dokunmadan CLI stdout, Telegram `sendMessage` (4096 karakter bölmeli) ve Web teslimat adaptasyonu; hedef kanal yetkilendirme doğrulaması.
   - Web standardı `AbortSignal` ve `AbortController` iptal mekanizmaları.
5. **Zamanlayıcı ve Kuyruk Destekli Single-Flight (`src/yusufclaw/routines/scheduler.py`)**:
   - `RoutineScheduler`:
     - Zamanlayıcı seviyesinde single-flight: Eşzamanlı `tick()` çağrıları aynı Task nesnesini döner (`first is second`).
     - Bellek durumuna dayalı dakikalık deduplikasyon (`memory.get_state(key) >= minute`).
     - Teslimat hatasında bir sonraki döngüde önbellekteki model yanıtıyla tekrar deneme.
     - Başarılı teslimatta SQLite/Postgres durum güncellemesi (`memory.set_state`).
     - `SingleFlightQueue`: Aynı rutin ID'sinin eşzamanlı olarak iki kez çalışmasını önleyen kuyruk destekli karşılıklı dışlama (mutex) kontrolü.
   - `create_routine_scheduler` / `createRoutineScheduler` fabrikası.

### 9.3 Test ve Doğrulama
- **Pytest**: **71 testin tamamı** yeşil geçti (`uv run pytest`, 6.19s).
- **Ruff**: 0 hata (`uv run ruff check .` -> `All checks passed!`).
- **Mypy**: 0 hata (`uv run mypy src` -> `Success: no issues found in 41 source files`).
- **Dokunulmayan Alanlar**: `src/yusufclaw/gateway`, `security`, `tools`, `memory` ve `packages/` %100 temiz ve dokunulmamıştır.
- **Kılavuz Belgesi**: `PY-SKILLS.md`.

---

## 10. Python OpenTelemetry Dağıtık İzleme ve Metrik Alt Sistemi — `py-otel` Dalı

### 10.1 Genel Mimari ve Kararlar
- `docs/ARCHITECTURE-v2.md` Katman 8 ("Verification & governance: verifier with acceptance criteria, rootless-container sandbox, permissions, approvals, reversible action log, OpenTelemetry tracing (tokens, latency, errors per agent)") ve P1 OTel yol haritası hedefleri eksiksiz olarak `src/yusufclaw/telemetry/` altında uygulanmıştır.
- **Kapsam İzolasyonu**: `src/yusufclaw/gateway`, `security`, `tools`, `memory`, `skills`, `routines` ve `packages/` (TypeScript) dizinlerine kesinlikle dokunulmamıştır. Yalnızca `src/yusufclaw/agent.py` içinde asenkron context manager sarmalayıcıları (`trace_agent_turn`, `trace_model_call`, `trace_tool_call`, `trace_approval_wait`) ile minimal çağrı noktaları bağlanmıştır.
- **Offline & Sıfır Ağ Bağımlılığı**: Varsayılan kurulumda (`otlp_endpoint` boş olduğunda) hiçbir harici ağ/soket çağrısı yapılmaz; güvenli bellek içi/no-op davranışı korunur. Tüm birim ve entegrasyon testleri `InMemorySpanExporter` ve `InMemoryMetricReader` ile sıfır ağ bağlantısıyla deterministik olarak çalışır.

### 10.2 Gerçekleştirilen Bileşenler
1. **OpenTelemetry Sağlayıcı Yönetimi (`src/yusufclaw/telemetry/__init__.py`)**:
   - `setup_telemetry(service_name, otlp_endpoint, span_exporter, metric_reader, force_reinit)`:
     - `Resource.create({"service.name": service_name})`.
     - `span_exporter` (örn. testlerde `InMemorySpanExporter`) verildiğinde `SimpleSpanProcessor` ile doğrudan bağlama.
     - `otlp_endpoint` verildiğinde HTTP protobuff standardına (`/v1/traces`, `/v1/metrics`) otomatik normalize ederek `BatchSpanProcessor(OTLPSpanExporter)` ve `PeriodicExportingMetricReader(OTLPMetricExporter)` devreye alma.
     - Uç nokta verilmediğinde sıfır dış bağımlılıklı offline `TracerProvider` ve `MeterProvider`.
   - `reset_telemetry()`: Testler arasında durum izolasyonu sağlayan temizleme aracı.
   - `get_tracer(name)` ve `get_meter(name)`: Etkin veya global sağlayıcıdan doğrudan örnek üreten erişimciler.
2. **Semantik Standartlar ve Nitelikler (`src/yusufclaw/telemetry/attributes.py`)**:
   - OpenTelemetry GenAI Semantic Conventions (`gen_ai.*`): `gen_ai.system`, `gen_ai.request.model`, `gen_ai.operation.name` ("chat"), `gen_ai.usage.prompt_tokens`, `gen_ai.usage.completion_tokens`, `gen_ai.usage.total_tokens`, `gen_ai.request.max_tokens`, `gen_ai.request.temperature`.
   - YusufClaw Alan Nitelikleri: `yusufclaw.session_id`, `yusufclaw.request_id`, `yusufclaw.turn.step_count`, `yusufclaw.turn.status`, `yusufclaw.tool.name`, `yusufclaw.tool.call_id`, `yusufclaw.tool.status` ("success" | "error" | "policy_denied" | "approval_denied"), `yusufclaw.tool.is_guarded`, `yusufclaw.approval.id`, `yusufclaw.approval.tool_name`, `yusufclaw.approval.status`.
   - Standart Span İsimleri: `yusufclaw.agent.turn`, `yusufclaw.model.complete`, `yusufclaw.tool.execute`, `yusufclaw.approval.wait`.
3. **Metrik Enstrümanları ve Kayıt Defteri (`src/yusufclaw/telemetry/metrics.py`)**:
   - `agent.turn.duration`: Histogram (`ms`), ajan turn yürütme süreleri.
   - `agent.turns.total`: Counter (`1`), oturum ve duruma göre turn sayıları.
   - `model.call.duration`: Histogram (`ms`), sağlayıcı tamamlama gecikmeleri.
   - `model.tokens.total`: Counter (`1`), modele ve türe (`prompt`, `completion`, `total`) göre tüketilen token miktarı.
   - `tool.execution.duration`: Histogram (`ms`), araç yürütme gecikmeleri.
   - `tool.executions.total`: Counter (`1`), araca ve duruma göre çağrı adetleri.
   - `approval.wait.duration`: Histogram (`ms`), insan onay bekleme gecikmeleri.
   - `approval.requests.total`: Counter (`1`), onay istekleri ve kararları.
4. **Asenkron İzleme Sarmalayıcıları (`src/yusufclaw/telemetry/instrumentation.py`)**:
   - `trace_agent_turn`: Turn yaşam döngüsü, süre ölçümü, hata yakalama ve durum etiketleme.
   - `trace_model_call` & `record_model_response`: Model çağrısı gecikmesi, token sayımı ve hata kaydı.
   - `trace_tool_call`: Araç bazında yürütme süresi ve sonuç durumu (`ToolSpanContext`).
   - `trace_approval_wait`: ActionCard insan onayı bekleme süresi ve onay/ret takibi (`ApprovalSpanContext`).
5. **LangGraph Ajan Döngüsü Entegrasyonu (`src/yusufclaw/agent.py`)**:
   - `Agent.run_state` ve `Agent.handle`: Turn başlangıcında kök `yusufclaw.agent.turn` span'ı açar; oturum ve istek kimliklerini ekler.
   - `model_node`: `provider.complete` çağrısını `trace_model_call` ile izler ve `record_model_response` ile token kullanımını işler.
   - `tool_node`: Deterministik güvenlik politikası retlerini (`policy_denied`), insan onayı bekleme süresini (`trace_approval_wait`), onay retlerini (`approval_denied`) ve araç yürütmelerini (`trace_tool_call`) iç içe hiyerarşik span'lar olarak bağlar.

### 10.3 Test ve Doğrulama
- **Pytest**: `tests/python/test_telemetry.py` (10 kapsamlı test senaryosu) eklendi; **104 testin tamamı** yeşil geçti (`uv run pytest`, 6.30s).
- **Ruff**: 0 hata, tam uyum (`uv run ruff check .` -> `All checks passed!`).
- **Mypy**: 0 hata, 49 kaynak dosyasında tam strict tip denetimi (`uv run mypy src` -> `Success: no issues found in 49 source files`).
- **Dokunulmayan Alanlar**: `src/yusufclaw/gateway`, `security`, `tools`, `memory`, `skills`, `routines`, `packages/` ve AWS altyapısı %100 temiz ve dokunulmamıştır.
- **Kılavuz Belgesi**: `PY-OTEL.md`.

## 11. Python AWS CDK Altyapı Taşıması (`py-infra` Dalı)

### 11.1 Genel Mimari ve Kararlar
- TypeScript referansındaki `packages/infra` AWS CDK altyapısı, Python 3.12 ve AWS CDK v2 mimarisine uygun olarak bağımsız bir paket halinde `infra_py/` altına taşınmıştır.
- **VALIDATE ONLY Kuralı**: Canlıya kesinlikle deploy yapılmamış (`cdk deploy` veya `aws cloudformation deploy` çağrılmamıştır). Tüm doğrulama `cdk synth`, unit testler (`aws-cdk assertions` / `pytest`), `ruff` ve `mypy` üzerinden gerçekleştirilmiştir.
- **Maliyet ve Bütçe Koruması**:
  - AWS Profile: `yusufclaw`
  - AWS Region: `us-east-1`
  - Aylık Bütçe Sınırı: **$50 USD**
  - **0 NAT Gateway Mimarisi**: Klasik NAT Gateway'lerin aylık maliyeti (~$32.40/adet) tüm bütçeyi boşta harcayacağı için, ECS Fargate ağır işler VPC'si 0 NAT Gateway ve genel alt ağlar (`assignPublicIp: true`) ile kurgulanmıştır.
  - **$0 Boşta Maliyet**: DynamoDB `PAY_PER_REQUEST` on-demand modu ile kullanılmadığı anlarda sıfır maliyet hedeflenmiştir.
- **Dokunulmayan Alanlar**: `src/yusufclaw/gateway`, `security`, `tools`, `memory`, `skills`, `routines`, `agent.py`, `packages/core|gateway|models|tools` (TS) %100 temiz ve dokunulmamış olarak korunmuştur.

### 11.2 Gerçekleştirilen Mimari Bileşenler
1. **Paket ve Yapılandırma (`infra_py/pyproject.toml`, `infra_py/cdk.json`, `infra_py/infra_py/config.py`)**:
   - Python `>=3.12`, `aws-cdk-lib>=2.100.0`, `constructs>=10.0.0,<11.0.0` bağımlılıkları.
   - Pydantic/Dataclass tabanlı strongly-typed `YusufClawConfig` ve `DEFAULT_CONFIG`.
   - `cdk.json` içinde profile `yusufclaw`, v2 özellik bayrakları ve `python3 app.py` başlatıcısı.
2. **DynamoDB Tek-Tablo Bellek Yapısı (`infra_py/infra_py/constructs/database.py`)**:
   - `yusufclaw-memory` tablosu; PK `pk`, SK `sk`.
   - `PAY_PER_REQUEST` faturalandırma modu.
   - `ttl` niteliğinde TTL etkinleştirme.
   - GSI1 (`gsi1pk`, `gsi1sk`, projection `ALL`) tersine arama ve filtreleme dizini.
   - Point-in-Time Recovery (`PointInTimeRecoverySpecification`) etkinleştirilmiş.
   - `grant_read_data`, `grant_write_data`, `grant_read_write_data` izin metotları.
3. **S3 Dosya ve Yapay Bellek Deposu (`infra_py/infra_py/constructs/storage.py`)**:
   - `yusufclaw-files-${AWS::AccountId}-${AWS::Region}` kovası.
   - S3-managed SSE şifreleme (`AES256`), `BLOCK_ALL` genel erişim engeli, versiyonlama.
   - Zorunlu SSL (`aws:SecureTransport: false` deny bucket policy).
   - Yaşam döngüsü kuralları: `temp/` dizini 14 gün sonra, eski versiyonlar 30 gün sonra otomatik silinir; yarım kalan multipart yüklemeleri 7 gün sonra iptal edilir.
   - Araç entegrasyonu için CORS yapılandırması.
4. **Amazon Bedrock IAM Yetkileri (`infra_py/infra_py/constructs/bedrock_iam.py`)**:
   - `BedrockInvokeManagedPolicy` managed policy: `InvokeModel`, `InvokeModelWithResponseStream`, `GetFoundationModel`, `ListFoundationModels`, `GetInferenceProfile`, `ListInferenceProfiles`, `ApplyGuardrail`.
   - Temel modeller (`arn:aws:bedrock:*::foundation-model/*`) ve çapraz bölge çıkarım profillerini kapsar.
5. **ECS Fargate Ağır Görevler Alt Sistemi (`infra_py/infra_py/constructs/heavy_jobs.py`)**:
   - 0 NAT Gateway maliyet korumalı VPC (`max_azs=2`, `nat_gateways=0`, `Public` subnets).
   - ECS Cluster (`yusufclaw-heavy-jobs-cluster`, Container Insights v2 devre dışı bırakılarak CloudWatch metrik ücretlerinden tasarruf edilmiştir).
   - CloudWatch Log Group (`/ecs/yusufclaw-heavy-jobs`, 30 gün saklama).
   - Ayrı `execution_role` ve `task_role` (DynamoDB, S3, Bedrock yetkileri verilmiş).
   - 1 vCPU (1024), 2048 MB RAM Fargate Task Definition ve `yusufclaw-heavy-runner` konteyneri.
   - `grant_run_task` ile Lambda'ya `ecs:RunTask` ve `iam:PassRole` yetkisi.
6. **API Gateway & Telegram Webhook Lambda (`infra_py/infra_py/constructs/telegram_webhook.py`, `infra_py/lambda/telegram_webhook/index.py`)**:
   - Regional REST API (`yusufclaw-telegram-api`), 50 req/s rate limit ve 100 burst kapasitesi.
   - `/webhook/telegram` POST ve GET metotları.
   - Python 3.12 ARM64 Graviton Lambda (`yusufclaw-telegram-webhook`), 512MB RAM, 60s zaman aşımı.
   - Python Lambda işleyicisi: GET sağlık kontrolü (200 OK), POST güncelleme ayrıştırma, `x-telegram-bot-api-secret-token` doğrulama, hızlı 200 OK yanıtı, bozuk verilerde sonsuz tekrar döngüsünü önleme (200 `{"ok": false}`).
7. **AWS Budgets $50/Ay Bütçe Koruması (`infra_py/infra_py/constructs/cost_budget.py`)**:
   - Aylık $50 USD bütçe sınırı (`yusufclaw-monthly-50-usd-budget`).
   - Çok katmanlı bildirimler: %50 gerçekleşen ($25), %80 gerçekleşen ($40), %100 gerçekleşen ($50), %100 tahmin edilen ($50 erken uyarı).
   - `yusufclaw-budget-alerts` SNS Topic ve yetki politikası; e-posta abonesi (`alerts@yusufclaw.local`).
8. **Stack Orkestrasyonu ve Çıkışlar (`infra_py/infra_py/stack.py`, `infra_py/app.py`)**:
   - 10 adet CloudFormation Output ve Export (`yusufclaw-telegram-webhook-url`, `yusufclaw-dynamodb-table-name`, `yusufclaw-files-bucket-name`, `yusufclaw-ecs-cluster-name`, `yusufclaw-fargate-task-def-arn`, `yusufclaw-budget-alert-topic-arn` vb.).
   - Kaynak etiketleri: `Project=YusufClaw`, `ManagedBy=AWS-CDK`, `Owner=yusuf`, `Budget=50USD`.

### 11.3 Test ve Doğrulama
- **CloudFormation Sentez Eşdeğerliği (`cdk synth`)**:
  - TypeScript CDK referansı ile Python CDK çıktısı karşılaştırılmış:
  - **Toplam Kaynak:** Her iki yığında da birebir **49 CloudFormation Kaynağı** (DynamoDB, S3, IAM, VPC, Subnets, ECS, Lambda, API GW, SNS, Budgets).
  - **Çıkışlar:** 10 adet CloudFormation çıkışı ve export isimleri %100 örtüşmektedir.
- **Birim Testleri (`infra_py/tests/test_infra_stack.py`)**:
  - `aws_cdk.assertions` (`Template`, `Match`) ve Python Lambda birim testleri.
  - **14 testin tamamı** offline başarıyla geçti (`uv run pytest`, 11.91s).
- **Kod Kalitesi ve Tip Denetimi**:
  - `uv run ruff check .` -> `All checks passed!` (0 hata, 0 uyarı).
  - `uv run mypy infra_py tests lambda` -> `Success: no issues found in 13 source files` (Strict mod, 0 hata).
- **Kılavuz Belgesi**: `PY-INFRA.md`.

---

## 12. Python Adaptif Orkestrasyon ve Doğrulama Alt Sistemi (`py-orch` Dalı)

### 12.1 Genel Mimari ve Kararlar
- `docs/ARCHITECTURE-v2.md` Katman 2 (Meta-orchestrator: task analyzer, budget manager, model routing) ve Katman 8 (Verification & governance: verifier with acceptance criteria, OTel tracing) ile `docs/RESEARCH.md` ve `docs/RESEARCH-ASIST.md` araştırmalarındaki ilkeler eksiksiz olarak yeni bir paket olan `src/yusufclaw/orchestration/` altında uygulanmıştır.
- **Kapsam İzolasyonu**: `src/yusufclaw/gateway`, `security`, `memory`, `skills`, `routines`, `tools`, `models/` içleri, `infra_py/` ve `packages/` (TypeScript) dizinlerine **kesinlikle dokunulmamıştır**.
- **Minimal Entegrasyon**: `src/yusufclaw/agent.py` içine yalnızca `enable_orchestration: bool = False` ve `orchestration_config: OrchestrationConfig | None = None` parametreleri eklenmiş; varsayılan durumda (`enable_orchestration=False`) mevcut davranış %100 birebir korunmuştur.
- **Deep Agents & LangGraph**: LangChain'in resmi `deepagents` pakitesi `uv` ile eklenmiş; ayrıca sıfır dış ağ bağlantısıyla deterministik test edilebilen yerel LangGraph durum makinesi işçi motoru (`SubagentRunner`) geliştirilmiştir.
- **Offline & Sıfır Ağ Bağımlılığı**: Testlerin tamamı `FakeProvider` ve bellek içi OTel dışa aktarıcısı (`InMemorySpanExporter`) ile harici ağ, AWS veya API anahtarı gerektirmeden çalışmaktadır.

### 12.2 Gerçekleştirilen Mimari Bileşenler
1. **Veri Modelleri (`src/yusufclaw/orchestration/models.py`)**:
   - Pydantic v2 tabanlı strongly-typed veri modelleri:
     - `TaskAnalysis`: `complexity` (`simple` | `medium` | `complex`), `required_tools`, `risk` (`low` | `medium` | `high` | `critical`), `plan_required`, `delegate_required`, `strategy` (`direct` | `plan_and_execute` | `delegate`), `reasoning`, `subtasks`, `estimated_steps`.
     - `BudgetLimits` & `BudgetUsage`: Turn ve oturum bazında token (prompt, completion, total), maliyet ($ USD), adım sayısı (`steps`) ve geçen süre (`elapsed_seconds`).
     - `TokenPricing`: Milyon token başına maliyet tablosu (varsayılan prompt $3.00/1M, completion $15.00/1M).
     - `BudgetStatus` & `BudgetExceededError`: Kalan bütçe payları ve tavan aşımlarında yapılandırılmış durum raporu.
     - `AcceptanceCriteria` & `VerificationResult`: Kural tabanlı sınır değerler, model hakemi yapılandırması, puan (`score`), telafi istemi (`retry_recommended`, `retry_prompt`).
     - `SubagentSpec` & `SubagentResult`: AOrchestra / CaMeL uyumlu alt ajan spesifikasyonu (talimat, güvenilmeyen veri kanalı, izinli araçlar, bütçe sınırları).
     - `PlanStep` & `ExecutionPlan`: Çok adımlı planlama ve durum takibi.
     - `OrchestrationConfig`: Master orkestrasyon yapılandırması.
2. **Görev Çözümleyici (`src/yusufclaw/orchestration/task_analyzer.py`)**:
   - `TaskAnalyzer`:
     - Deterministik Sezgisel Motor: Metin boyutu, numaralandırılmış adım listeleri (`1. ... 2. ...`), ardışık bağlaçlar (`then`, `after that`, `ve ardından`), araç tespiti (`shell`, `file_read`, `file_write`, `web_fetch`, `memory`) ve risk kalıpları (`rm -rf`, `drop table`, `sudo`, `token`, `password`).
     - Model Destekli Mod: `ModelProvider` üzerinden JSON şemalı çıkarım; modelin yanıt veremediği veya geçersiz JSON döndürdüğü durumlarda istisna fırlatmadan derhal deterministik sezgisel motora güvenli geri düşüş (`graceful fallback`).
     - Dağıtık İzleme: `yusufclaw.orchestration.task_analysis` span'ı ile karmaşıklık, risk ve strateji özniteliklerinin kaydı.
3. **Bütçe Yöneticisi (`src/yusufclaw/orchestration/budget.py`)**:
   - `BudgetManager`:
     - Hem tek turn (`turn_usage`) hem de kümülatif oturum (`session_usage`) düzeyinde bağımsız tüketim takibi.
     - Token fiyatlandırması üzerinden kesin maliyet hesabı.
     - `check_limits()`: Tavan aşıldığında `is_exceeded=True` ve ayrıntılı ihlal nedeni üretimi.
     - `get_graceful_stop_message()`: Bütçe tükendiğinde sistemi çökertmeden kullanıcıya korunan durumu ve kısmi çıktıyı bildiren zarif durdurma yanıtı.
     - Dağıtık İzleme: `yusufclaw.orchestration.budget_check` span'ı.
4. **Doğrulayıcı (`src/yusufclaw/orchestration/verifier.py`)**:
   - `Verifier`:
     - Kural Tabanlı Doğrulama: `min_length`, `max_length`, `required_keywords` (büyük/küçük harf duyarsız), `forbidden_keywords`, `required_pattern` (regex), `must_not_contain_patterns`, `expected_tools` (turn içinde çağrılması zorunlu araçlar).
     - Model Hakemi: `model_judge_required=True` durumunda JSON rubrik değerlendirmesi ile semantik puanlama.
     - Tek Tur Otomatik Düzeltme: Doğrulama başarısız olduğunda ve bütçe/deneme hakkı varken (`max_retries=1`) spesifik eksiklikleri içeren `retry_prompt` oluşturarak ajana tam 1 kez telafi hakkı tanıma.
     - Dağıtık İzleme: `yusufclaw.orchestration.verification` span'ı.
5. **Alt Ajan Delegasyonu (`src/yusufclaw/orchestration/subagent.py`)**:
   - `SubagentRunner`:
     - **En Fazla 1 Derinlik**: Alt ajanın başka bir alt ajan başlatması engellenir (`spec.max_depth = 1`).
     - **En Az Ayrıcalıklı Kesişim**: Alt ajana yalnızca ebeveynin izin verdiği araçlar arasından talep edilenler verilir (yetki genişletme kesinlikle engellenir).
     - **Veri Kanalı Ayrımı**: Kullanıcı talimatı güvenilir istem kanalından, bağlam verileri ise güvenilmeyen veri etiketli referans bloğundan aktarılır (multi-agent prompt injection koruması).
     - **Bütçe Düşümü**: Alt ajanın tükettiği token ve adımlar doğrudan ebeveyn `BudgetManager` hesabından düşülür.
     - Dağıtık İzleme: `yusufclaw.orchestration.subagent` span'ı.
6. **Planla ve Yürüt İş Akışı (`src/yusufclaw/orchestration/planner.py`)**:
   - `PlanAndExecuteWorkflow`:
     - LangGraph `StateGraph` tabanlı durum makinesi (`planner` -> `execute_step` -> `synthesizer`).
     - Planlama Düğümü: Görevi `PlanStep` dizisine dönüştürür.
     - Yürütme Düğümü: Her adımı doğrudan veya `SubagentRunner` specialist işçisine delege eder. Her adım öncesinde bütçe kontrolü yapar; bütçe aşılırsa zarif şekilde sentez düğümüne yönlendirir.
     - Adım Doğrulama: Adım çıktısını `Verifier` ile kontrol eder, gerekirse 1 tur telafi dener.
     - Sentez Düğümü: Tamamlanan adımların bulgularını birleştirerek kapsamlı nihai yanıt üretir.
7. **Meta-Orkestratör Koordinasyonu (`src/yusufclaw/orchestration/orchestrator.py`)**:
   - `AdaptiveOrchestrator`:
     - Turn başında bütçe kontrolü yapar; tavan aşılmışsa doğrudan zarif durdurma döner.
     - Görev Çözümleyiciyi çalıştırır; stratejiye göre ("direct" vs "plan_and_execute" / "delegate") yönlendirir.
     - Çıktıyı `Verifier` ile denetler; eksiklik varsa tek tur düzeltme döngüsünü tetikler.
     - Turn sonunda bütçeyi günceller ve dağıtık izleme span'larını (`yusufclaw.orchestration.turn`) tamamlar.
8. **LangGraph Ajan Entegrasyonu (`src/yusufclaw/agent.py`)**:
   - `Agent.__init__` içinde `enable_orchestration: bool = False` ve `orchestration_config`.
   - `Agent.run` ve `Agent.handle`: Bayrak aktif olduğunda `self.orchestrator.orchestrate_turn` üzerinden çalışır; bayrak kapalıyken eski kod yolu aynen korunur.

### 12.3 Test ve Doğrulama
- **Port Edilen ve Eklenen Testler (`tests/python/test_orchestration.py`)**:
  - 20 yeni kapsamlı birim ve entegrasyon testi eklendi:
    - Sezgisel ve model destekli görev çözümleme testleri (basit, orta, karmaşık, araçlı, yüksek/kritik risk).
    - Model analiz hatasında deterministik motora geri düşüş.
    - Bütçe token, maliyet ve adım hesaplama testleri.
    - Turn ve oturum sınır aşımları, zarif durdurma mesajı ve istisna mekanizması.
    - Kural tabanlı kabul kriterleri ve model hakemi doğrulama testleri.
    - Doğrulama başarısızlığında 1 tur telafi istemi üretimi.
    - Alt ajan derinlik kısıtlaması (1'e sabitleme) ve ebeveyn araç kesişimi.
    - LangGraph planla-ve-yürüt durum makinesi testi.
    - Adaptif orkestratör doğrudan yol ve doğrulama telafi entegrasyonu.
    - Ajan `enable_orchestration` bayrak denetimi (kapalı ve açık durumlar).
    - OpenTelemetry izleme span'larının (`InMemorySpanExporter`) doğrulanması.
- **Sonuçlar**:
  - `uv run pytest`: **124 testin tamamı** offline, sahte sağlayıcılarla ve sıfır dış ağ bağlantısıyla yeşil geçti (~10.4s).
  - `uv run ruff check .`: 0 hata, 0 uyarı (`All checks passed!`).
  - `uv run mypy src`: 0 hata, 57 kaynak dosyasında tam strict tip denetimi (`Success: no issues found in 57 source files`).
  - `packages/` (TypeScript), `gateway/`, `security/`, `memory/`, `skills/`, `routines/`, `tools/`, `models/` ve `infra_py/` %100 temiz ve dokunulmamıştır.
- **Kılavuz Belgesi**: `PY-ORCH.md`.

---

## 13. Python MCP İstemcisi, Rootless Sandbox ve Beceri Yaşam Döngüsü — `py-mcp` Dalı

### 13.1 Genel Mimari ve Kararlar
- `docs/ARCHITECTURE-v2.md` Katman 6 (Skills: task -> solve -> test -> extract method -> skill -> retest -> version git; pinning + quarantine), Katman 7 (Tools: MCP resmi SDK; >30 araçta dinamik seçim) ve Katman 8 (Verification & governance: rootless-container sandbox, limits, reversible action log) hedefleri eksiksiz olarak `src/yusufclaw/mcp/`, `src/yusufclaw/sandbox/` ve `src/yusufclaw/skills/lifecycle.py` altında uygulanmıştır.
- **Kapsam ve İzolasyon Güvencesi**:
  - Kesinlikle dokunulmayan modüller: `agent.py`, `gateway/`, `security/`, `memory/`, `routines/`, `telemetry/`, `models/`, `infra_py/`, `src/yusufclaw/tools/*.py` ve `packages/` (TypeScript).
  - `src/yusufclaw/skills/loader.py` dosyasına **kesinlikle dokunulmamış**; beceri yaşam döngüsü bağımsız olarak `src/yusufclaw/skills/lifecycle.py` içinde kurgulanmıştır.
- **Offline ve Sıfır Dış Bağımlılık Garantisi**:
  - Tüm MCP testleri yerel stdio alt süreç mock sunucusu (`MCPServer`) ile 100% offline çalıştırılmıştır.
  - Sandbox testleri ortamda yetkisiz kullanıcı ad alanları (unprivileged user namespaces) veya rootless konteyner bulunmadığında otomatik olarak güvenle atlanmakta (`skipif`), fail-closed denied fallback mimarisi doğrulanmaktadır.

### 13.2 Gerçekleştirilen Bileşenler
1. **Resmi Python SDK Destekli MCP İstemcisi (`src/yusufclaw/mcp/`)**:
   - `models.py`: `MCPServerConfig` (stdio command/args/env/cwd ve remote streamable HTTP / SSE url/headers desteği, sürüm sabitleme, `pinned_hash`, karantina bayrağı), `MCPConfig` dosya ve sözlük yükleyicisi, `ServerPinRecord`.
   - `pinning.py`: `compute_tools_hash` (araç adı, açıklama ve girdi şemasını kanonik JSON ile deterministik SHA-256 özetine dönüştürür), `PinningStore` (`mcp.lock.json` takibi). Araç açıklaması zehirlenmesi (description poisoning) veya yeni unpinned sunucu tespit edildiğinde sunucu otomatik olarak `quarantined` durumuna alınır.
   - `client.py`: Resmi `mcp` SDK v2.3.0 (`ClientSession`, `stdio_client`, `streamable_http_client`, `sse_client`) sarmalayıcısı; zaman aşımı ve metin çıktısı ayrıştırma.
   - `adapter.py`: MCP araçlarını YusufClaw `GuardedTool` arayüzüne uyarlar. `ToolManifest(source="mcp", effects=["read_untrusted"])` üretir. Karantinadaki sunucuların yürütülmesi fail-closed olarak engellenir (`QuarantinedServerError`).
   - `selection.py`: `DynamicToolSelector` ve `select_tools_for_query`. Araç sayısı 30'u aştığında RAG-MCP tarzı BM25 / token benzerlik puanlamasıyla sorguyla en alakalı top-$k$ (varsayılan 10) aracı dinamik olarak seçer, çekirdek araçları (`always_include`) korur ve model bağlamı şişmesini (prompt bloat) önler.
2. **Rootless Sandbox Koşucusu ve Geri Alınabilir Eylem Günlüğü (`src/yusufclaw/sandbox/`)**:
   - `models.py`: `SandboxConfig` (512MB RAM, 1.0 CPU kotası, 30s zaman aşımı, 64KB çıktı kesintisi, varsayılan kapalı ağ), `SandboxResult`, `ActionLogEntry`, `UndoResult`.
   - `runner.py`: `SandboxRunner` soyut arayüzü; `BubblewrapRunner` (`bwrap --unshare-net --unshare-ipc --unshare-pid --unshare-uts --ro-bind /usr ... --bind workspace ... --tmpfs /tmp`); `RootlessContainerRunner` (Podman / Docker rootless `--network none --memory --cpus --read-only`); `DeniedFallbackRunner` (sandbox bulunmadığında ana sistemi riske atmamak için çalıştırmayı fail-closed olarak reddeden çıkış kodu 126 kapısı); `get_sandbox_runner` otomatik tespit fabrikası.
   - `action_log.py`: `ReversibleActionLog` ile append-only JSONL formatında eylem günlüğü (`.data/workspace/.yusufclaw_actions.jsonl`). Her yan etki UUID, zaman damgası, araç adı, eylem türü, hedef ve geri alma ipucu (`undo_hint`: `delete_file`, `restore_file`, `remove_dir`) ile kaydedilir. `undo()` çağrıldığında ters işlem uygulanır ve olay kaynaklı (event-sourced) tazminat kaydı düşülerek mükerrer geri almalar engellenir.
3. **7-Aşamalı Beceri Yaşam Döngüsü Hattı (`src/yusufclaw/skills/lifecycle.py`)**:
   - Pipeline Akışı: `task -> solve -> test -> extract -> skill -> retest -> version (git-backed)`.
   - `TaskSpec`: Hedef, istem ve kabul kriterleri.
   - `TaskSolution`: Gözlemlenen çözüm adımları, çıktı ve metodoloji.
   - `VerificationResult`: Kriter uygunluk doğrulaması ve puanı.
   - `SkillCandidate`: YAML frontmatter (`name`, `description`, `allowed-tools`, `version`, `resources`) ve yapılandırılmış markdown gövdesi ile beceri sentezi.
   - `skill`: `skills/quarantine/{name}/SKILL.md` altına taslak/karantina durumunda SHA-256 özetiyle yazım.
   - `retest`: Aday beceri enjekte edilerek görevin baştan çözülebilirliğinin doğrulanması (başarısızlıkta `rejected` durumunda karantinada kalır).
   - `version`: Doğrulanan beceriyi `skills/{name}/SKILL.md` altına yükseltir, `skills.lock` içine SHA-256 sabitlemesi yapar ve git reposunda commit oluşturarak commit SHA'sını kaydeder.
   - `SkillLock`: `skills.lock` dosyasında beceri sabitlemesi ve tahrifat denetimi (`hash_mismatch`).

### 13.3 Test ve Doğrulama
- **Eklenen Test Dosyaları**:
  - `tests/python/test_mcp.py` (5 senaryo: config, canonical hash determinizmi, pinning/karantina, offline mock stdio MCP sunucu tam döngüsü, >30 dinamik araç seçimi).
  - `tests/python/test_sandbox.py` (7 senaryo: config sınırları, bwrap komut inşası, rootless container komut inşası, denied fallback fail-closed, runner tespiti, bwrap izolasyon probe, append-only geri alınabilir eylem günlüğü yaşam döngüsü).
  - `tests/python/test_skill_lifecycle.py` (4 senaryo: frontmatter biçimlendirme, `SkillLock` tahrifat tespiti, 7-aşamalı git commitli tam pipeline döngüsü, retest başarısızlığında karantinada kalma).
- **Sonuçlar**:
  - `uv run pytest`: **119 passed, 1 skipped** (18.94s, 21 test dosyasında 120 test, 0 hata).
  - `uv run ruff check .`: 0 hata, 0 uyarı (`All checks passed!`).
  - `uv run mypy src`: 60 kaynak dosyasında tam strict modda 0 hata.
- **Kılavuz Belgesi**: `PY-MCP.md`.

---

## 14. Rol Tabanlı Adaptif Model Yönlendirme (Model Router) — `py-routing` Dalı

### 14.1 Genel Mimari ve Kararlar
- `docs/ARCHITECTURE-v2.md` Katman 2 (Meta-orchestrator: model routing: strong model for planning/synthesis, cheap/local Ollama for classify/search/transform) ve `PY-ORCH.md` hedefleri doğrultusunda, tek sağlayıcıya bağımlı orkestrasyon hattı rol tabanlı çoklu model yönlendirme mimarisine yükseltilmiştir.
- **Kapsam ve İzolasyon Güvencesi**:
  - Kesinlikle dokunulmayan modüller: `gateway/`, `security/`, `memory/`, `mcp/`, `sandbox/`, `skills/lifecycle.py`, `routines/`, `infra_py/` ve `packages/` (TypeScript).
  - Katkılar `src/yusufclaw/models/` altında modüler olarak eklenmiş (`router.py`, `__init__.py`), `src/yusufclaw/telemetry/` içine OTel yönlendirme semantikleri işlenmiş, `src/yusufclaw/orchestration/` ve `src/yusufclaw/agent.py` içine ise geriye dönük %100 uyumlu hafif kancalar (light hooks) yerleştirilmiştir.
- **Offline ve Deterministik Çalışma Güvencesi**:
  - Tek sağlayıcılı geriye dönük uyumluluk ve basamaklı geri çekilme (cascade fallback): Ortamda yalnızca tek bir `FakeProvider` yapılandırıldığında router tüm roller için otomatik ve hatasız olarak bu sağlayıcıya döner.
  - Harici ağ, AWS bağlantısı veya API anahtarı gerektirmeden CI/test ortamlarında %100 offline çalışır.

### 14.2 Gerçekleştirilen Bileşenler
1. **İşlevsel Roller ve Yönlendirme Yapılandırması (`src/yusufclaw/models/router.py`)**:
   - `ModelRole` (`StrEnum`): `planner`, `synthesis`, `judge`, `classify`, `search`, `transform`, `heuristic-assist`, `default`, `strong`, `cheap`.
   - `RoutingConfig`: Rollerin sağlayıcı isimlerine eşlenmesi (`role_routes`), güçlü ve ucuz sağlayıcı takma adları (`strong_provider="strong"`, `cheap_provider="cheap"`), basit ve düşük riskli görevlerde ucuz sağlayıcıyı doğrudan tercih etme bayrağı (`prefer_cheap_for_simple`), ve ortam değişkenlerinden otomatik yükleme (`RoutingConfig.from_env()`, `YC_ROUTING_*`).
   - `ModelRouter`: Çoklu sağlayıcı kayıt yöneticisi (`register_provider`, `set_route`), görev analizi (`TaskAnalysis`) odaklı dinamik rol kaydırma, basamaklı çözümleme (mapped role -> provider alias -> default -> sole provider fallback), `ModelProvider` polimorfizmi (`router.complete(req)`).
2. **Orkestrasyon Entegrasyonu (`src/yusufclaw/orchestration/`)**:
   - `TaskAnalyzer`: Model destekli görev sınıflandırmasında (`analyze_model`) güçlü model yerine ucuz/yerel sınıflandırıcı (`ModelRole.CLASSIFY`) sağlayıcısını kullanır; pahalı model token tüketimini önler.
   - `Verifier`: Model hakemi değerlendirmesinde (`_evaluate_model_judge`) yüksek muhakeme gücüne sahip güçlü modeli (`ModelRole.JUDGE`) kullanır.
   - `PlanAndExecuteWorkflow`: Plan üretimi (`_generate_model_plan`) ve nihai sentez (`synthesizer_node`) aşamalarında güçlü modeli (`ModelRole.PLANNER` ve `ModelRole.SYNTHESIS`), adımların doğrudan yürütülmesinde `ModelRole.DEFAULT`, başarısız adım telafisinde ise `ModelRole.STRONG` kullanır.
   - `SubagentRunner`: Alt ajanlar `spec.model_role` alanı üzerinden router ile dinamik sağlayıcı çözümler.
   - `AdaptiveOrchestrator`: Tüm orkestrasyon yaşam döngüsünü router ile koordine eder. Görev karmaşıklığı basit (`complexity=simple`) ve risk düşük (`risk=low`) olduğunda, doğrudan dönüşü ucuz sağlayıcıya yönlendirir (`routed_direct_provider`). Doğrulama başarısızlığında 1-turluk telafiyi güçlü sağlayıcıya devreder.
   - `Agent` (`src/yusufclaw/agent.py`): `AgentState` içine `active_provider` alanı eklenerek doğrudan dönüş çağrılarının yönlendirilen sağlayıcıyla yürütülmesi sağlandı; geriye dönük uyumluluk korundu.
3. **OpenTelemetry İzleme Semantikleri (`src/yusufclaw/telemetry/`)**:
   - Nitelikler: `yusufclaw.routing.role`, `yusufclaw.routing.provider`, `yusufclaw.routing.target`, `yusufclaw.routing.reason`.
   - Yardımcı: `record_routing_decision(span, role, provider_name, target, reason)`.
   - `yusufclaw.orchestration.task_analysis`, `plan_generation`, `synthesis`, `verification`, `turn` ve `subagent` span'ları yönlendirme nitelikleriyle zenginleştirildi.

### 14.3 Test ve Doğrulama
- **Eklenen Test Dosyası**:
  - `tests/python/test_routing.py` (19 senaryo: rol enum ve varsayılan eşlemeler, env parsing, güçlü/ucuz rol seçimi, tek sağlayıcı basamaklı geri çekilme, eksik sağlayıcı toleransı, dinamik sağlayıcı kaydı ve rota güncelleme, TaskAnalysis odaklı ucuz yol seçimi, TaskAnalyzer sınıflandırma izolasyonu, Verifier hakem izolasyonu, Planner ve Synthesizer güçlü model ayrımı, AdaptiveOrchestrator çoklu sağlayıcı tam turu ve ucuz doğrudan yol, Agent entegrasyonu, OpenTelemetry span nitelik doğrulaması).
- **Sonuçlar**:
  - `uv run pytest`: **158 passed, 1 skipped** (21.02s, 22 test dosyasında 159 test, %100 yeşil).
  - `uv run ruff check .`: 0 hata, 0 uyarı (`All checks passed!`).
  - `uv run mypy src`: 69 kaynak dosyasında tam strict modda 0 hata (`Success: no issues found in 69 source files`).
  - Yasaklı dizinler (`gateway/`, `security/`, `memory/`, `mcp/`, `sandbox/`, `skills/lifecycle.py`, `routines/`, `infra_py/`, `packages/`) tertemiz korunmuştur.
- **Kılavuz Belgesi**: `PY-ROUTING.md`.

---

## 15. Python AG-UI 1.0 SSE Olay Akışı, YC-05 Web Onay Entegrasyonu ve Web UI — `py-agui` Dalı

### 15.1 Genel Mimari ve Kararlar
- `docs/ARCHITECTURE-v2.md` Interfaces katmanı (Web AG-UI 1.0) ve `PY-GATEWAY.md` hedefleri doğrultusunda, Web Gateway (`src/yusufclaw/gateway/web.py`) üzerindeki taslak SSE akışı (`progress -> message -> done`) tam teşekküllü, gerçek zamanlı ve gözlemlenebilir bir AG-UI 1.0 olay akışına dönüştürülmüştür.
- **Kapsam ve İzolasyon Güvencesi**:
  - Kesinlikle dokunulmayan modüller: `security/`, `memory/`, `orchestration/`, `mcp/`, `sandbox/`, `skills/`, `routines/`, `infra_py/` ve TypeScript `packages/`.
  - Değiştirilen dosyalar yalnızca: `src/yusufclaw/gateway/web.py`, `src/yusufclaw/gateway/web_page.py`, `src/yusufclaw/gateway/approval.py`, `src/yusufclaw/agent.py` (yalnızca opsiyonel turn hook) ve `tests/python/test_web_gateway.py`.
- **Güvenlik ve İstemci Bütünlüğü Standartları**:
  - **Sıkı CSP**: `default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'`.
  - **XSS ve DOM Güvenliği**: Dinamik veriler asla `innerHTML` veya `eval` ile işlenmez; tüm çıktılar `textContent` ve `document.createElement` üzerinden güvenle basılır.
  - **Depolama İzolasyonu**: Tarayıcı tarafında hiçbir hassas veri `localStorage` veya `sessionStorage` içinde saklanmaz.
  - **Yetkilendirme ve Köken Denetimi**: `Authorization: Bearer <token>` ve `Origin` / `Sec-Fetch-Site` doğrulaması korunur; istek gövdesi 100KB ile sınırlandırılır.
  - **Veri Maskeleme Motoru**: `sanitize_secret_patterns` ve `sanitize_args_summary` ile Bearer token'ları, AWS erişim anahtarları (`AKIA...`), genel API anahtarları (`ghp_...`, `sk-...`, `glpat-...`), URL temel kimlik bilgileri ve gizli anahtar/parola alanları (`[REDACTED]`) SSE akışına çıkmadan önce temizlenir; argümanlar 300 karakter ile sınırlandırılır.

### 15.2 Gerçekleştirilen Bileşenler
1. **YC-05 İçi Bellek Onay Sağlayıcısı (`src/yusufclaw/gateway/approval.py`)**:
   - `InMemoryApprovalProvider`: `@runtime_checkable class ApprovalProvider(Protocol)` arayüzünü uygular.
   - Her `ActionCard` için kriptografik rastgele tek kullanımlık nonce (`secrets.token_hex(16)`) ve kanonik SHA-256 argüman özeti üretir.
   - **Tek Kullanımlık Replay Koruması**: Onaylanmış veya reddedilmiş kartların yeniden kullanılması engellenir (HTTP 400 `Replay detected`).
   - **Tahrifat Tespiti**: Girdi parametreleri imzalanan özetten farklıysa işlem derhal reddedilir (HTTP 400 `Action arguments have been tampered with`).
   - **Red Kaydı ve Süre Aşımı**: Reddedilen eylemler kalıcı olarak kaydedilir; süresi dolan kartlar geçersiz kılınır.
2. **Asenkron Geri Bildirim ve Turn Olay Kancası (`src/yusufclaw/agent.py`)**:
   - `TurnEventHook` (`ContextVar` tabanlı `set_turn_event_hook`, `get_turn_event_hook`): Eşzamanlı isteklerde global durum kirlenmesi olmadan istek başına olay dinleyicisi sağlar.
   - `Agent.handle(..., on_event=...)` ve `Agent.run(..., on_event=...)` kancaları.
   - `model_node`: Model sağlayıcısı akışı (`stream`) desteklediğinde `text_delta` olayları yayar; desteklemediğinde tam yanıtı tek seferde üretir.
   - `tool_node`: Araç çalıştırma başlangıcında `tool_call_start`, onay gerektiğinde `approval_requested` ve tamamlanmada `tool_call_end` olayları yayar.
   - Kanca tanımlanmadığında mevcut çekirdek agent davranışı %100 farksız ve ek yük getirmeden çalışır.
3. **Web Gateway AG-UI 1.0 SSE Akışı ve Uç Noktaları (`src/yusufclaw/gateway/web.py`)**:
   - `POST /api/chat/stream`: Eşzamanlı `asyncio.Queue` ile agent dönüşünü ve iç kanca olaylarını birleştirir.
   - **Olay Akış Hiyerarşisi**:
     * `RunStarted`: `requestId`, `conversationId`, `timestamp`.
     * `progress`: Geriye dönük uyumluluk durum bildirimleri.
     * `ToolCallStart`, `ToolCallArgs` (maskeli özet), `ToolCallEnd` (durum ve süre).
     * `ActionCard` / `approval`: YC-05 onay kartı verisi.
     * `TextMessageStart`, `TextMessageContent` (artımlı belirteçler), `TextMessageEnd`.
     * `message` & `done`: Eski istemcilerle tam geriye dönük uyumluluk.
     * `RunFinished`: İşlem tamamlama yaşam döngüsü.
     * `error`: Hata kodu ve temizlenmiş hata mesajı.
   - `POST /api/chat/approval` ve `POST /api/approval`: Web onay uç noktaları (`ApprovalInput` modeliyle `approvalId`, `nonce`, `action`/`decision` kabul eder; `extra="forbid"` ile yabancı alanları reddeder).
4. **Web Arayüzü İstemcisi (`src/yusufclaw/gateway/web_page.py`)**:
   - `CHAT_PAGE`: `#approvals` ve `#tools` kapsayıcıları eklendi.
   - `CHAT_STYLE`: Kart stilleri, yeşil onay butonu, kırmızı red butonu, araç çalıştırma etiketleri.
   - `CHAT_SCRIPT`: `ReadableStream` SSE ayrıştırıcısı ile `RunStarted`, `TextMessageContent` (harf harf ekleme), `ToolCallStart/Args/End` ve `ActionCard` kartlarının canlı çizimi; Onayla / Reddet butonları tıklandığında `/api/chat/approval` uç noktasına güvenli POST gönderimi.

### 15.3 Test ve Doğrulama
- **Genişletilen Test Dosyası**: `tests/python/test_web_gateway.py` (13 kapsamlı test senaryosu):
  1. `test_web_auth_cross_origin_validation_and_token_free_page`: Token doğrulaması, köken kontrolü ve tokensiz HTML sunumu.
  2. `test_web_chat_success_and_error_sanitization`: Standart sohbet ve hata temizleme.
  3. `test_web_scheduled_replies_delivery_and_inbox`: Zamanlanmış rutin gelen kutusu teslimatı.
  4. `test_web_adapter_requires_token_and_socket_lifecycle`: WebAdapter soket yaşam döngüsü.
  5. `test_web_ag_ui_sse_streaming`: Eski SSE akış formatı uyumluluğu.
  6. `test_web_ag_ui_1_0_run_lifecycle_and_fallback`: `RunStarted` -> `TextMessageStart/Content/End` -> `RunFinished` döngüsü ve fallback akışı.
  7. `test_web_ag_ui_1_0_mid_turn_incremental_streaming`: Gerçek zamanlı artımlı belirteç akışı.
  8. `test_web_ag_ui_1_0_tool_calls_and_secret_masking`: Araç çağrı yaşam döngüsü ve Bearer/AWS/Github anahtar maskeleme doğrulaması.
  9. `test_web_ag_ui_1_0_interactive_approval_lifecycle_and_yc05_checks`: `ActionCard` akışı, web onay uç noktası, tek kullanımlık nonce, replay engelleme ve tahrifat reddi.
  10. `test_web_ag_ui_1_0_denial_lifecycle`: `deny` aksiyonunun işlenmesi, red kaydı ve sonradan onayın engellenmesi.
  11. `test_web_ui_rendered_elements_and_approval_controls`: HTML ve JS içerik denetimi; CSP uyumluluğu, `innerHTML`, `localStorage` ve `sessionStorage` bulunmadığının kanıtı.
  12. `test_sanitize_secret_patterns_and_args_summary`: Regex maskeleme ve sözlük/liste argüman temizleme testi.
  13. `test_approval_input_validation`: Pydantic girdi doğrulaması, alias desteği ve yabancı alan reddi.
- **Doğrulama Sonuçları**:
  - `uv run pytest`: **147 passed, 1 skipped** (25.41s, 0 hata, %100 offline).
  - `uv run ruff check .`: 0 hata, 0 uyarı (`All checks passed!`).
  - `uv run mypy src`: 68 kaynak dosyasında tam strict tip denetimi (`Success: no issues found in 68 source files`).
  - `MYPYPATH=src uv run mypy tests/python/test_web_gateway.py`: 0 hata.
- **Kılavuz Belgesi**: `PY-AGUI.md`.

---

## 16. Python UX Spesifikasyonu ve Kullanıcı Entegrasyonu Tasarımı (`py-ux` Dalı)

### 16.1 Genel Mimari ve Kararlar
- Grok Bot asistan deneyimi model alınarak hazırlanan, araştırma raporlarına (`docs/RESEARCH.md`, `docs/RESEARCH-ASIST.md`, `docs/DESIGN-RECOMMENDATIONS.md`, `docs/ARCHITECTURE-v2.md`) %100 dayandırılmış kapsamlı kullanıcı entegrasyonu tasarım belgesi `docs/UX-SPEC.md` yazılmıştır.
- **Birincil Arayüz**: Web UI (UI ile backend arasında AG-UI 1.0 protokolü zorunlu).
- **Tamamlayıcı Kanallar**: Telegram botu (mobil/acil kanal) ve Terminal CLI (geliştirici/otomasyon kanalı), merkezi gateway ve paylaşılan durum sahipliği ([ADR-01]).
- **Sıkı Araştırma Dayanağı**: Tüm tasarım kararları literatürdeki makale veya rapor bölümüne atıf yapacak şekilde kurgulanmış; literatürde doğrudan karşılığı bulunmayan mekanizmalar (ör. tarayıcı canlı akışında interaktif CAPTCHA devralma) açıkça `[unbacked - proposal]` olarak etiketlenmiştir.
- **Yalnızca Dokümantasyon Kuralı**: Uygulama kodu yazılmamış (`docs only`), mevcut kod tabanı ve testler %100 korunmuştur.

### 16.2 Kapsanan Grok-Bot Özellikleri ve AG-UI Olay Protokolü
1. **Çoklu Adlandırılmış Ajanlar Kenar Çubuğu**: `roles/` altındaki rol kataloğu (`ROLE.md`) ile eşleşen, bağımsız oturum ve bellek kapsamına sahip uzmanlaşmış ajanlar.
2. **Anında Bilişsel Teyit & Arka Plan Kilometre Taşları**: Kullanıcı mesajına <300ms içinde teyit dönüp girdiyi serbest bırakan, ardından adım adım ilerleyen durum göstergesi (`run.acknowledged`, `run.milestone`).
3. **Satır İçi Karar & Seçenek Widget'ları**: Serbest metin yerine yapılandırılmış seçenekler sunan interaktif çipler ve onay kutuları (`widget.question`, `action.resolve_widget`).
4. **Varsayılan Taslak Eylem Kartları (VAC)**: Dış dünyayı etkileyen tüm eylemlerde modelin serbest metninden bağımsız, gerçek argümanlardan üretilen, nonce ve SHA-256 korumalı onay kartları (`widget.approval_card`).
5. **Maskelenmiş Gizli Bilgi Formları**: Parola ve API anahtarlarının sohbet geçmişine düşmesini engelleyen geçici maskeli modal ve gizli bilgi aracısı.
6. **OAuth & MCP Bağlayıcı Merkezi**: Gmail, Calendar, Drive, GitHub, Notion, Slack entegrasyonları, açıklama hash'i sabitleme ve karantina mekanizması.
7. **Rutinler & Otomasyon Paneli**: IANA saat dilimli cron oluşturucu, yetki zarfı ve misfire politikası (`routine.triggered`).
8. **Görünür & Düzenlenebilir Bellek Yöneticisi**: Profil ve olgusal iddia tablosu, kanıt kimlikleri ve FTS/vektör indekslerinden gerçek unlearning yapan `/forget` akışı.
9. **Beceri (Skills) Kütüphanesi**: Agent Skills açık standardına uygun YAML ön maddesi, `skills.lock` bütünlük denetimi ve karantina vitrini.
10. **Ekler & İnteraktif Artefakt Kanvası**: Kod, Markdown ve görsel çıktılar için çift panelli bölünmüş ekran ve görsel fark (diff) görüntüleyici.
11. **Çok Kanallı Bildirim Motoru**: Web push, masaüstü toast/ses ve Telegram çapraz kanal onay eşitlemesi.
12. **'Computer' Sandbox & Canlı Tarayıcı Görünümü**: Playwright ve Bubblewrap ortamının JPEG/DOM akışı, egress güvenlik duvarı uyarıları.
13. **Ajanlar Arası (A2A) Takım Arkadaşları**: Derinlik-1 kısıtlı delegasyon hiyerarşisi, ayrılmış veri/talimat kanalları ve bütçe devri.
14. **Anlık Kullanım, Token & Maliyet Sayacı**: Turn ve oturum token/maliyet ($ USD) göstergesi, $50 aylık AWS bütçe tavanı çubuğu ve OTel Gantt izleme çekmecesi.

### 16.3 Sıralı Uygulama İş Listesi
- **Sprint 1 (Temeller)**: UX-01 (AG-UI SSE motoru), UX-02 (Ajanlar kenar çubuğu), UX-03 (Bilişsel teyit & kilometre taşları), UX-04 (Satır içi soru widget'ları).
- **Sprint 2 (Güvenlik & Bellek)**: UX-05 (ActionCard arayüzü), UX-06 (Maskeli gizli bilgi modalı), UX-07 (Bellek denetim & forget kanvası).
- **Sprint 3 (Otomasyon & Yetenekler)**: UX-08 (Rutinler paneli), UX-09 (Artefakt kanvası & diff), UX-10 (Computer sandbox & screencast), UX-11 (MCP bağlayıcı merkezi), UX-12 (A2A visualizer), UX-13 (Kullanım ve maliyet sayacı).

### 16.4 Doğrulama
- Dokümantasyon bütünlüğü kontrol edildi.
- Test çalıştırma veya kod değişikliği yapılmadı.

---

## 17. Python Model Streaming Onarımı ve Dağıtık İzleme Entegrasyonu (`py-streamfix` Dalı)

### 17.1 Problem Tanımı ve Kök Neden Analizi
- **Sorun 1 (Kayıp Araç Çağrıları)**: `py-agui` dalında eklenen model akış (streaming) kod yolunda, model yanıtı yalnızca ham metin parçalarından (`ModelResponse(text="".join(accumulated_text))`) tekrar oluşturuluyordu. Modelin akış sırasında ürettiği `tool_calls` bilgisi düşüyor, `AIMessage` nesnesine araç çağrıları aktarılamadığı için LangGraph ajan döngüsü `tool_node` adımına geçemeden `END` durumuna yönleniyordu.
- **Sorun 2 (Eksik OTel Dağıtık İzleme)**: `trace_model_call` sarmalayıcısı yalnızca `else` (non-streaming) bloğunda çalıştırıldığı için, akış yapan modellerde OpenTelemetry span'ları (`yusufclaw.model.complete`), token metrikleri ve model yönlendirme öznitelikleri (`yusufclaw.routing.provider`, `yusufclaw.routing.role`, `gen_ai.request.model`) tamamen atlanıyordu.

### 17.2 Gerçekleştirilen Mimari Çözümler
1. **Sağlayıcı Akış Arayüzü ve Veri Modelleri (`src/yusufclaw/models/types.py`)**:
   - `ToolCallDelta`: Artımlı araç çağrısı parçaları (`index`, `id`, `name`, `args_delta`, `input`).
   - `StreamChunk`: Normalleştirilmiş akış nesnesi (`text`, `tool_call_delta`, `tool_calls`, `usage`, `response`).
   - `StreamAccumulator`: Akıştan gelen metin parçacıklarını, parça parça biriken JSON araç çağrılarını ve kullanım bilgilerini asenkron olarak toplayıp tek bir nihai `ModelResponse` nesnesine dönüştüren güvenli toplayıcı.
   - `ModelProvider.stream(request)`: Sağlayıcılar için standart asenkron üreteç protokolü.
2. **Ajan Döngüsü Model Düğümü Onarımı (`src/yusufclaw/agent.py`)**:
   - `trace_model_call(active_p, request)` sarmalayıcısı hem akış hem de tek parça model çağrılarını kapsayacak şekilde kök düzeye çekildi.
   - Akış sırasında `text_delta` (AG-UI token akışı), `tool_call_delta` ve `tool_call` turn kancaları gerçek zamanlı olarak tetiklenirken, arka planda `StreamAccumulator` ile tam yanıt inşa edildi.
   - Akış tamamlandığında `record_model_response` ile token ve süre metrikleri span'a eksiksiz işlendi.
   - `stream` implemente etmeyen veya `NotImplementedError` fırlatan sağlayıcılar için tek seferlik `complete()` çağrısına zarif geri düşüş (`graceful fallback`) korundu.
3. **Sağlayıcı Adaptörleri Güncellemesi (`src/yusufclaw/models/`)**:
   - `FakeProvider` & `FakeStreamingProvider`: Testler ve offline ortamlar için deterministik akış simülatörü.
   - `OpenAIProvider.stream`: OpenAI Chat Completions streaming ve araç çağrı parçacığı ayrıştırması.
   - `OllamaProvider.stream`: Yerel Ollama `/api/chat` SSE JSON satır akışı ve araç çağrısı yakalama.
   - `BedrockProvider.stream`: AWS Bedrock `converse_stream` API blok ve araç kullanım olayları eşleyicisi.
   - `ModelRouter.stream`: Yönlendirilen sağlayıcıya akış delegasyonu.
4. **Dağıtık İzleme ve Yönlendirme Semantikleri (`src/yusufclaw/telemetry/instrumentation.py`)**:
   - `_resolve_model_metadata` ve `trace_model_call`: `ModelRouter` veya yönlendirilmiş sağlayıcı örneği üzerindeki `yusufclaw.routing.provider`, `yusufclaw.routing.role` ve `gen_ai.request.model` niteliklerinin akış span'larına da eksiksiz yazılması sağlandı.

### 17.3 Test ve Doğrulama
- **Eklenen Testler (`tests/python/test_streaming.py`)**:
  - `test_streaming_provider_emits_text_and_tool_call_in_order`: Metin ve araç çağrısı üreten akış sağlayıcısının aracı çalıştırması, AG-UI olaylarının kronolojik sırası (`text_delta` -> `tool_call_start` -> `tool_call_end` -> `text_delta`) ve OTel span kaydı.
  - `test_streaming_with_tool_call_deltas`: Parçalı JSON argümanlarının birleşimi ve araç yürütümü.
  - `test_streaming_with_routed_provider_attributes_on_span`: Model yönlendirme özniteliklerinin akış span'larında doğrulanması.
  - `test_streaming_fallback_when_not_implemented`: `NotImplementedError` durumunda geriye dönük uyumluluk kontrolü.
  - `test_stream_accumulator_direct`: Birim düzeyinde toplayıcı doğrulaması.
  - `test_openai_provider_stream_mocked`, `test_ollama_provider_stream_mocked`, `test_bedrock_provider_stream_mocked`: Mock sağlayıcı akış testleri.
- **Sonuçlar**:
  - `uv run pytest`: **174 passed, 1 skipped** (baseline 166 + 8 yeni streaming testi, %100 yeşil).
  - `uv run ruff check .`: 0 hata, 0 uyarı (`All checks passed!`).
  - `uv run mypy src`: 0 hata, 69 kaynak dosyasında tam strict tip denetimi.
  - İzolasyon güvencesi: `roles/`, `src/yusufclaw/roles/` ve `gateway/web.py` dosyalarına dokunulmadı.

---

## 18. Uzman Ajan Rol Kataloğu, Açık Yetenek Matrisleri ve Politika Yürütümü — `py-roles` Dalı

### 18.1 Genel Mimari ve Kararlar
- **Tasarım İlkesi**: Tek ve genel amaçlı devasa bir model yerine, `docs/ARCHITECTURE-v2.md` Katman 2 (Meta-orchestrator) ve Katman 3 (Subagents: least-privilege tools, read-only by default, sandbox execution) prensiplerine uygun, yetenek sınırları kesin olarak çizilmiş uzman ajan rol kataloğu (`CapabilityMatrix`) oluşturulmuştur.
- **Kapsam ve En Az Yetki (Least Privilege)**:
  - 8 uzman rol tanımlanmıştır: `orchestrator`, `coder`, `writer`, `mail`, `browser`, `researcher`, `ops`, `scheduler`.
  - Her uzman rol için:
    - İzin verilen araçlar (`allowed_tools`) ve MCP sunucuları (`allowed_mcp_servers`).
    - Beceriler (`skills`).
    - Model katmanı (`model_tier`: `strong`, `cheap`, `local`).
    - Bellek okuma/yazma sınırları (`memory_scopes`: `read`, `write`).
    - Risk seviyesi (`risk_level`: `low`, `medium`, `high`, `critical`).
    - İnsan onayı gerektiren eylemler (`approval_actions`).
    - Bütçe tavanları (`budget_limits`: token, maliyet, adım ve süre limitleri).
    - Rootless sandbox izolasyon zorunluluğu (`sandbox_required`, `sandbox_mode`).
    - Dış iletişim kanalları (`send_channels`) ve **`send = always approval` kuralı**: Bir rolün dış dünyaya (Telegram, Web, Email vb.) mesaj göndermesi her koşulda insan onayı gerektiren bir `ActionCard` üretir; izinsiz kanal gönderimleri fail-closed olarak reddedilir.
    - A2A 1.0 (Agent-to-Agent) ajan keşif kartı alanları (`A2AAgentCard`).
- **Mimari İzolasyon**: `memory/`, `security/`, `skills/`, `routines/`, `telemetry/`, `models/`, `infra_py/` ve `packages/` (TypeScript) modüllerine dokunulmamış; rol mimarisi bağımsız ve tam uyumlu olarak `src/yusufclaw/roles/` altında inşa edilmiştir.

### 18.2 Gerçekleştirilen Bileşenler
1. **Pydantic Yetenek Matrisi Şeması (`src/yusufclaw/roles/models.py`)**:
   - `CapabilityMatrix`: Rol kimliği (`role_id`), görünen isim, açıklama, takma adlar (`aliases`), araç/MCP izin listeleri, beceriler, model katmanı, bellek sınırları, risk seviyesi, onay gerektiren eylemler, bütçe sınırları, sandbox gereksinimi, kanal izinleri ve A2A kartı.
   - `MemoryScopeConfig`: `can_read(scope)` ve `can_write(scope)` yöntemleriyle joker karakter (`*`) veya tam kapsam eşleşmesi desteği.
   - `A2AAgentCard`: A2A 1.0 protokolü ile tam uyumlu; yetenekler, girdi/çıktı modları, beceriler ve semantik sürümleme içerir.
   - `RoleRoutingDecision`: Görev çözümleme sonucunda seçilen birincil rol, ikincil roller, güven puanı ve gerekçelendirme.
2. **8 Uzman Rolün YAML Katalog Tanımları (`roles/catalog/` ve `src/yusufclaw/roles/catalog/`)**:
   - `orchestrator.yaml`: Grok Bot tarzı ana asistan; planlama, alt ajan delegasyonu, rutin otomasyonu ve bellek koordinasyonu yapar (`model_tier: strong`, `risk_level: medium`).
   - `coder.yaml`: Codex tarzı sandboxed yazılım mühendisliği uzmanı; repo düzenleme, test yürütme, git işlemleri (`sandbox_required: true`, `risk_level: high`, `git_push` onay kapılı).
   - `writer.yaml`: Dokümantasyon, rapor ve e-posta taslağı uzmanı; taslak hazırlar ama asla doğrudan dışarı mesaj göndermez (`send_channels: []`, `risk_level: low`).
   - `mail.yaml`: Gelen kutusu okuma, önceliklendirme ve taslak yanıt uzmanı; e-posta gönderimi kesin olarak insan onayına bağlıdır (`send_channels: [email]`, `approval_actions: [mail_send]`).
   - `browser.yaml` (alias: `web`): Web gezintisi, form doldurma ve araştırma uzmanı; form gönderimleri ve satın alma/ödeme işlemleri insan onayına bağlıdır (`sandbox_required: true`, `risk_level: medium`).
   - `researcher.yaml`: Akademik ve bilimsel literatür derin araştırma uzmanı; arXiv, Semantic Scholar taraması ve kanıt/atıf doğrulaması yapar (`model_tier: strong`, `risk_level: low`).
   - `ops.yaml` (alias: `devops`): Bulut altyapısı ve DevOps uzmanı; varsayılan olarak yalnızca doğrulama (validate-only) modunda çalışır; canlı dağıtım ve kaynak silme kesin olarak insan onayına bağlıdır (`risk_level: critical`).
   - `scheduler.yaml`: Takvim, hatırlatıcı ve ajanda uzmanı; toplantı ve hatırlatıcıları yönetir (`model_tier: cheap`, `risk_level: low`).
3. **Katalog Yükleyicisi ve Doğrulayıcı (`src/yusufclaw/roles/loader.py`)**:
   - `RoleCatalog`: YAML dosyalarını otomatik tespit eder, şema doğrulamasından geçirir, mükerrer rol kimliklerini denetler, takma ad haritasını (`alias_map`) kurar.
   - `get_role(name_or_alias)`: Büyük/küçük harf duyarsız ve takma ad destekli rol erişimi (`web` -> `browser`, `devops` -> `ops`).
   - `validate_catalog()`: Tüm rollerin güvenlik kurallarını ve sandbox tutarlılığını doğrular.
4. **Görev Çözümleyicili Rol Yönlendiricisi (`src/yusufclaw/roles/router.py`)**:
   - `RoleRouter`: `TaskAnalyzer` sınıfından gelen karmaşıklık, risk, gerekli araçlar ve alt görev çözümlemelerini etki alanı örüntüleriyle birleştirir.
   - Tekil görevlerde doğrudan en uygun uzman role (`coder`, `writer`, `mail`, `browser`, `researcher`, `ops`, `scheduler`) yönlendirir.
   - Çok adımlı delegasyon görevlerinde `orchestrator` rolünü birincil yönetici, ilgili uzmanları ise ikincil alt ajanlar olarak tayin eder.
5. **Politika Yürütüm Motoru (`src/yusufclaw/roles/enforcement.py`)**:
   - `RolePolicyEnforcer`: Güvenlik katmanında her araç çağrısını deterministik olarak denetler:
     - Yetkisiz araç çağrıları: Fail-closed olarak reddedilir (`PolicyDecision(kind='deny')`).
     - Yetkisiz MCP sunucuları: Reddedilir.
     - Bellek kapsam ihlalleri: Yetkisiz okuma veya yazma denemeleri engellenir.
     - Dış kanal gönderimleri: Yetkisiz kanallar engellenir; izinli kanallarda **`send = always approval`** kuralı işletilerek `ActionCard` üretilir.
     - Onay kapılı eylemler: Doğrudan insan onayına sunulur.
     - Geçerli çağrılar: Monotonik Progent/FIDES temel güvenlik denetimlerine (`decide`) delege edilir.
6. **Web Ağ Geçidi ve AG-UI Durum Entegrasyonu (`src/yusufclaw/gateway/web.py`)**:
   - `GET /api/roles`: Yetkili kullanıcılara tüm uzman rolleri, yetenek matrislerini ve A2A kartlarını JSON formatında sunar.
   - `GET /api/events`: AG-UI SSE akışına ilk bağlantıda `roles` olayını basarak ön yüzün aktif rolleri tanımasını sağlar.
7. **Yetenek Matrisi Dokümantasyon Üreticisi (`src/yusufclaw/roles/generator.py`)**:
   - `docs/CAPABILITY-MATRIX.md`: 8 rolün tüm yeteneklerini karşılaştıran detaylı markdown tablosu, rol profilleri ve A2A JSON kartlarını otomatik oluşturur.

### 18.3 Test ve Doğrulama
- **Eklenen Test Dosyası (`tests/python/test_roles.py`)**:
  - 19 yeni birim ve entegrasyon testi:
    - Şema doğrulama, bellek kapsamı normalizasyonu ve A2A kart sentezi testleri.
    - Katalog 8 rol tam yükleme, takma ad ve bozuk YAML hata yönetimi testleri.
    - 8 etki alanında görev yönlendirme ve çok adımlı delegasyon orkestrasyonu testleri.
    - Politika yürütüm motorunda izin verilen/reddedilen araçlar, MCP sunucuları ve bellek sınırları testleri.
    - Harici kanal gönderimi reddi ve `send = always approval` eylem kartı testleri.
    - Yüksek riskli onay kapılı eylem testleri (`git_push`, `infra_deploy`, `browser_submit`).
    - Web ağ geçidi `/api/roles` uç noktası (401 yetkisiz ve 200 yetkili durumlar) ve `/api/events` SSE akışı testleri.
    - `docs/CAPABILITY-MATRIX.md` dokümantasyon üretici doğrulama testi.
- **Sonuçlar**:
  - `uv run pytest`: **158 passed, 1 skipped** (26.33s, 22 test dosyasında 159 test, 0 hata).
  - `uv run ruff check .`: 0 hata, 0 uyarı (`All checks passed!`).
  - `uv run mypy src`: 74 kaynak dosyasında tam strict modda 0 hata (`Success: no issues found in 74 source files`).
- **Kılavuz Belgesi**: `docs/CAPABILITY-MATRIX.md`.

---

## 19. Python Shared Coordination Substrate — `py-coord` Dalı

- Yeni `src/yusufclaw/coordination/` paketi: Pydantic modelleri ve async `publish`, `search`, `visit`, `list_events` API; Postgres adaptörü için `CoordinationStore` protokolü.
- SQLite WAL üzerinde append-only `publish/claim/result/artifact_ref` olayları, UUID blob deposu, SHA-256 bütünlük kontrolü ve atomik FTS5/BM25 indeksleme. UPDATE/DELETE trigger koruması, kapsam izolasyonu ve sıralı cursor pagination eklendi.
- Sıralama, atıf sayısı ve ziyaret sıklığı doğruluk sayılmaz. Arama sonuçları daima doğrulanmamıştır; opsiyonel async `Verifier` gerçek kanıtı kontrol eder. Başarısız doğrulama hata verir; karar yalnızca ilgili ziyaret için geçerlidir.
- 8 yeni test: publish/search/visit, yeniden açılışta kalıcılık, append-only koruması, kapsam ve rollback, eşzamanlı bağlantılar, metadata araması ve doğrulama sınırları.
- Doğrulama: `uv run pytest -q` → **201 passed, 1 skipped**; `uv run ruff check .` → temiz; `uv run mypy src` → **79 dosyada hata yok**.
- Kullanım, güven sınırları ve backend sözleşmesi: `docs/PY-COORD.md`. Agent, models, gateway/web.py, roles, queue ve infra_py değiştirilmedi; AWS dağıtımı yapılmadı. §17–18 korunmuştur.

---

## 20. Python Kalıcı İş Kuyruğu — `py-queue` Dalı

### 20.1 Mimari ve Kapsam
- Redis listesi yerine PostgreSQL kaynaklı kalıcı iş tablosu ve yerel geliştirme için SQLite fallback eklendi. Redis yalnızca isteğe bağlı uyandırma ipucu taşır; kesintisi iş kaybına yol açmaz.
- PostgreSQL claim işlemi `FOR UPDATE SKIP LOCKED` kullanır. Kullanıcı/rol eşzamanlılık ve bütçe kabulü kısa bir veritabanı kilidiyle atomiktir; handler çalışırken kilit tutulmaz. Kuyruk coordination ve memory altyapısından bağımsızdır.
- Durumlar: `queued`, `running`, `succeeded`, `failed`, `cancelled`, `waiting_approval`. Öncelik, UTC `run_at`, kullanıcı kapsamlı idempotency, üstel retry/backoff, lease/heartbeat, eski worker fencing ve dead-letter desteklenir.
- Kullanıcı/rol limitleri veritabanında saklanır. Bütçeler deneme başına muhafazakâr rezervasyonlarla ve yaşam boyu toplam üzerinden uygulanır; takvimsel sıfırlama veya gerçek maliyet iadesi yapılmaz. Subagent mevcut BudgetManager denetimlerini de korur.
- Parent/child bağlantıları aynı kullanıcıyla sınırlandırılır. Parent iptali alt işleri iptal eder; lease reclaim sonrası başarısız parent'ın açık alt işleri de durdurulur.

### 20.2 Entegrasyon ve İşletim
- `YUSUFCLAW_QUEUE_ENABLED=1`: rutin tick işleri kuyruğa yazar; sürekli scheduler bir asyncio worker başlatır. Delegasyonlar orchestration parent işi ve subagent child işleri üzerinden yürür. Bayrak üretimde varsayılan kapalı, testlerde varsayılan açıktır; eski rutin sözleşmesi testleri kapalı yolu ayrıca doğrular.
- `YUSUFCLAW_QUEUE_URL`: PostgreSQL DSN veya `sqlite+aiosqlite:///queue.sqlite`. Handler kayıtları güvenilir provider/grants bağlamıyla yapılır; Python callable veya kimlik bilgileri payload'a yazılmaz.
- `python -m yusufclaw.queue list|show|events|cancel`: yerel operatör CLI. AG-UI `CUSTOM` progress/durum olayları kalıcı cursor ile okunur. Worker OTel span'ları enqueue trace bağlamını sürdürür.
- Onay bekleme slotu bırakır; `resume` öncesi mevcut action-card doğrulaması çağıranın sorumluluğundadır. Harici yan etkiler için exactly-once garantisi verilmez; handler idempotency/receipt uygulamalıdır.
- Tek admission kilidi ve metadata taraması küçük kurulumlar içindir; yüksek ölçek için ayrı sayaçlar, indeksli politikalar ve retention gerekir. Bir denemelik orchestration/subagent işleri çökme sonrası dead-letter olur; plan checkpoint/replay bu kapsamda eklenmedi.
- Ayrıntılı kullanım ve sınırlar: `docs/PY-QUEUE.md`. `agent.py`, `models/`, `gateway/web.py`, `roles/` ve coordination dosyaları değiştirilmedi. AWS dağıtımı yapılmadı.

### 20.3 Doğrulama
- `uv run pytest -q && uv run ruff check . && uv run mypy src`: **214 passed, 1 skipped**; Ruff temiz; 78 kaynak dosyasında strict mypy temiz.
- Ayrı, geçici yerel PostgreSQL 16 konteynerinde aynı kuyruk testleri: **22 passed**. Canlı satır kilidi altında SKIP LOCKED, concurrent idempotency, durum geçişleri, retry, reclaim, iptal, budget/concurrency, worker shutdown, parent/child ve OTel bağlamı doğrulandı.
- Legacy Redis smoke testi artık opsiyonel wakeup ve Redis kesinti davranışını denetler. Testler AWS veya canlı model çağrısı gerektirmez.

---

## 21. Python Bellek Konsolidasyonu ve Bağlam Sıkıştırma — `py-memcons` Dalı

- Mevcut SQLite/Postgres bellek operasyonları üzerinde `MemoryStore` protokolü ve `ConsolidationJob`: yapılandırılabilir yakın-kopya eşleştirme, revision + gerekçeli forget, kaynak kimlikleri, kesinti sonrası devam ve açık kullanıcı günlük notlarından semantik fact terfisi. Güven yükseltilmez; kaynak notlar pasifleştirilir ve silinen fact tekrar türetilmez.
- `ContextCompactor`: token bütçesi aşımında eski tam turların özeti; sabit bilgiler, açık işler ve araç sonucu referansları korunur. Deterministik çevrimdışı özetleyici ve mevcut `ModelProvider` arayüzü üzerinden opsiyonel LLM özetleyici; hata halinde yerel fallback, korunmuş bağlam sığmazsa `over_budget` bildirimi. Kalıcı geçmiş değiştirilmez.
- `memory/context.py` içinde önem × ilgi × üstel yaş bozunması; her iki backend'in mevcut bağlam derlemesine sınırlı aday yeniden sıralaması. Mevcut API ve alias'lar korunur.
- `tests/python/test_memory_consolidation.py`: 27 çevrimdışı test vakası; iki backend arayüzü, audit/FTS, kapsam/güven, tekrar çalıştırma, terfi/unutma, sıralama, Unicode, araç bağımlılıkları ve bütçe/fallback. Gerçek Postgres gerekmez; async adaptör SQLite üzerinde test edilir.
- Doğrulama: `uv run pytest -q` → **228 passed, 1 skipped**; `uv run ruff check .` → temiz; `uv run mypy src` → **82 dosyada hata yok**.
- Kullanım ve sınırlar: `docs/PY-MEMCONS.md`. İş kapsam başına tek yazıcıyla, en fazla 200 aktif kayıt üzerinde çalışır; otomatik zamanlama/agent entegrasyonu eklenmedi. Agent, models, gateway, roles, coordination ve queue değiştirilmedi. AWS kullanılmadı; push yapılmadı. §20 queue worker için ayrılmıştır; mevcut bölümler yeniden numaralandırılmadı.

---

## 22. Python Çevrimdışı Değerlendirme Koşucusu — `py-evals` Dalı

- `evals/offline.py` ve `evals/offline_fixtures.json`: ajan çekirdeğini scripted/mock `ModelProvider` ile çevrimdışı çalıştırır (ağ, API anahtarı, AWS yok). Senaryolar: sayfa/araç çıktısı enjeksiyonu (sızdırma / onaysız tehlikeli araç), zararsız bellek/araç/rutin başarıları, görev başına onay kartı sayısı.
- Bilinen güvenlik boşlukları `expected_fail` / xfail ile yeşil koşucu korunur; ASR raporda tutulur. CI alt kümesi `tests/python/test_evals.py` içinde DNS/socket/subprocess yasaklıdır.
- Rapor: `evals/results/` JSON+markdown (`latest.*` gitignore; `sample.*` örnek). Mevcut YAML `evals/runner.py` güvenlik paketi ayrı tutuldu.
- Doğrulama: `uv run pytest -q` → **257 passed, 1 skipped, 1 xfailed**; ruff temiz; mypy src temiz. Kullanım: `docs/PY-EVALS.md`. Agent/models/gateway/roles/queue/memory/sandbox değiştirilmedi. AWS yok; push yapılmadı.

## 23. Python Rootless Sandbox Sertleştirme — `py-sandbox` Dalı

- Bubblewrap → rootless Podman → rootless Docker seçim sırası; varsayılan ağ kapalı, salt-okunur kök, açık workspace bind, CPU/bellek/süre/tasks limitleri, fail-closed (runtime yoksa 126). Gizli yollar (ssh, .env, cloud creds, agent-data) mount reddi; symlink/özel dosya reddi.
- `shell` aracı sandbox runner'ı tercih eder; host shell'e düşmez. İptal/timeout süreç grubunu öldürür; konteyner cleanup denemesi audite yazılır.
- `ReversibleActionLog`: intent+outcome (komut hash, backend, exit); ham komut/env/çıktı saklanmaz. Keyfi komutlar geri alınamaz olarak belgelenir.
- Doğrulama: `uv run pytest -q` → **273 passed**; ruff temiz; mypy src (85 dosya) temiz. Mock/skip ile bwrap/podman gerekmez. Kullanım: `docs/PY-SANDBOX.md`. Agent/models/gateway/roles/queue/memory/evals değiştirilmedi. AWS yok; push yapılmadı. §22 evals için ayrılmıştır.

## 24. Python Writer Delegeleri (External Coding CLIs) — `py-writers` Dalı

- Yeni `src/yusufclaw/writers/` paketi: Harici kodlama CLI'larını (`agy`, `codex`) izole, sınırlı ve denetimli alt işler olarak çalıştıran delege adaptörü (P2-01, YC-24).
- `CLIProtocolProbe`: `--version` denetimi ve semver ayrıştırması ile CLI ikilisinin varlığını ve çalışabilirlik protokolünü doğrular; bulunamayan veya hata veren ikililer fail-closed olarak erken aşamada reddedilir.
- `WorkspaceManager`: Yapılandırılabilir üst dizin altında (`YUSUFCLAW_WRITERS_WORKSPACE_DIR`, varsayılan `/tmp/yusufclaw-worktrees`, `0o700` izinli) izole git worktree yaşam döngüsü (`git worktree add --detach` veya geçici dal). `validate_workspace_path` ile dizin dışına taşma (`../`) ve symlink kaçışları engellenir. İş tamamlandığında veya iptal edildiğinde worktree ve geçici dal otomatik temizlenir (`git worktree remove --force` + `prune`).
- Güvenlik ve Ortam Arındırma (`scrub_environment`): Alt süreçler üst süreçteki hiçbir gizli bilgiyi (AWS, OpenAI, Anthropic, Gemini, GitHub, SSH, Database, Redis, Telegram, YusufClaw token/key) devralmaz. Yalnızca izinli güvenli ortam değişkenleri aktarılır ve deterministik git kimliği eklenir. `env_overrides` üzerinden gizli anahtar enjeksiyonu `SecurityViolationError` ile engellenir.
- `ChildBudget`: Süre (`max_time_seconds`, varsayılan 120s), çıktı tamponu (`max_output_bytes`, varsayılan 1MB) ve değişen dosya adedi (`max_changed_files`, varsayılan 100) tavanları uygulanır.
- Eşzamanlılık ve Derinlik (P2-02): Derinlik kesin olarak 1 ile sınırlıdır (`depth <= 1`); alt delegelerin özyinelemeli alt delege başlatması yasaktır. En fazla iki eşzamanlı alt süreç (`MAX_CONCURRENT_CHILDREN = 2`) `asyncio.Semaphore(2)` ile denetlenir; aşan talepler kuyruğa alınır.
- Ebeveyn Doğrulama Kancası (`WriterVerifier`): Bir delege işi yalnızca CLI çıkış kodu 0 olduğu için başarılı sayılmaz. Delege tamamlandıktan sonra worktree içinde ebeveyn doğrulama testi (varsayılan sanal ortam pytest'i veya `spec.test_command`) çalıştırılır; test başarısızsa veya zaman aşımına uğrarsa iş başarısız işaretlenir (`test_passed=False`).
- Yapılandırılmış Sonuç (`WriterResult`): Çıkış kodu, özet metin, `git status --porcelain` ve `git diff --name-only` ile toplanan göreceli değişen dosya yolları (`changed_paths`), test çıktısı, süre ve hata/çakışma/iptal bayrakları döndürülür.
- Çift Çalıştırma Kipi: `YUSUFCLAW_QUEUE_ENABLED=1` olduğunda `JobQueue` üzerinde `kind="writer"`, `role="writer"` alt işi olarak enqueue edilir ve `parent_id` ile hiyerarşik bağlanır; aksi halde eşzamanlılık semaforu ve izole süreç grubu (`start_new_session=True`) ile inline `asyncio` subprocess olarak yürütülür.
- İptal ve Çakışma: `asyncio.CancelledError` veya kuyruk iptali durumunda süreç grubuna `SIGTERM` gönderilir (`os.killpg`), worktree temizlenir ve `cancelled=True` bayrağı atanır. Çakışan çalışma dizinleri `WorkspaceConflictError` üretir.
- Doğrulama: `uv run pytest -q && uv run ruff check . && uv run mypy src` → **305 passed, 1 skipped, 1 xfailed**; ruff temiz; strict mypy temiz (92 kaynak dosyasında hata yok). Çevrimdışı test süiti `tests/python/test_writers.py` (25 test) canlı ağ veya bulut kimliği gerektirmez. Kullanım: `docs/PY-WRITERS.md`. Agent core, models, gateway, roles, coordination, memory, sandbox, evals değiştirilmedi. AWS yok; push yapılmadı. §§1–23 korunmuştur.

## 25. Python A2A Teammate Substrate ve Ayrık Veri Kanalları — `py-a2a` Dalı

- Yeni `src/yusufclaw/a2a/` paketi: Agent-to-Agent 1.0 tarzı yerel takım arkadaşı protokolü (Google A2A bulut bağımlılığı olmadan aynı host üzerinde). Pydantic modelleri (`AgentCard`, `TeammateBudget`, `TaskRequest`, `TaskResult`, `ContextArtifact`), olaylar (`TeammateEvent`) ve `A2ATransport` protokolü.
- Ayrık Veri Kanalları (Segregated Data Channels): Alt ajan koordinatörün tam oturum ve olgusal belleğini (`SqliteMemory`, `PostgresMemory`, profile facts, turn geçmişi) kesinlikle okuyamaz; yalnızca `TaskRequest.context_artifacts` ile açıkça aktarılan veri nesnelerine erişebilir. Güvenilmeyen veri kanalı etiketlemesi ile CaMeL / FIDES prompt enjeksiyon izolasyonu ve `IsolatedChildMemory` sağlandı.
- Derinlik-1 ve Eşzamanlılık Yönetimi: `TeammateRegistry` yalnızca derinlik-1 alt ajanları takip eder; torun ajan oluşturma denemeleri (`depth > 1` veya çocuk ajan üzerinden alt ajan çağırma) fail-closed olarak `DepthLimitExceededError` ile engellenir. Ebeveyn başına azami 3 eşzamanlı çalışan sınırı (`ConcurrencyLimitExceededError`) uygulanır.
- Çocuk Başına Bütçe ve İptal Yayılımı: Her çocuk görev için bağımsız token, adım, süre ve maliyet tavanları (`TeammateBudget`) enforced edilir; ebeveyn görevi iptal edildiğinde (`cancel_parent`), altındaki tüm aktif çocuk görevler derhal iptal edilir.
- Taşıma Katmanları (Transports): Sıfır ağ maliyetli bellek içi `InProcessTransport` ve yerel decoupled süreçler için loopback `HttpJsonTransport` (`httpx.AsyncClient` + `create_a2a_router` FastAPI APIRouter adaptörü).
- UX-12 Telemetri Kancası: `SubagentRunner` içine ince kanca (`TeammateStatusHook`) bağlandı; web görselleştiricisi inşa edilmeden AG-UI UX-12 olayları (`teammate.spawned`, `teammate.step`, `teammate.finished`) yayınlanır.
- Testler ve Doğrulama: `tests/python/test_a2a.py` içinde 19 çevrimdışı test; veri kanalı izolasyonu, derinlik-1, bütçe tavanları, ASGI loopback HTTP, iptal yayılımı ve kanca olayları doğrulandı.
- Kalite ve Regresyon Kapısı: `uv run pytest -q` → **299 passed, 1 skipped, 1 xfailed**; `uv run ruff check .` → temiz; `uv run mypy src` → **93 kaynak dosyasında 0 hata**. Kullanım: `docs/PY-A2A.md`. `agent.py` çekirdeği, `models/`, `gateway/web.py` UI, rol YAML'ları, `writers/`, `memory/`, `sandbox/`, `evals/` ve queue store çekirdeği değiştirilmedi; AWS kullanılmadı; push yapılmadı. §24 writer delegeleri için ayrılmıştır.

## 26. AWS Dağıtımı (infra_py) — `py-deploy` Dalı

**Durum (2026-10-16):** Kod dağıtıma hazır, testler geçiyor; **`cdk bootstrap` henüz yapılmadı** çünkü root (`default`) profil oturumu süresi dolmuş (`aws login` gerekiyor) ve `yusufclaw-agent` kullanıcısının IAM rol oluşturma yetkisi yok. Bu yüzden `cdk deploy` çalıştırılmadı; aşağıdaki çıktılar dağıtımdan sonra doldurulacak.

### Mimari (tek stack: `YusufClawInfraStack`, us-east-1, etiket `Project=yusufclaw`)
- DynamoDB on-demand bellek tablosu, S3 dosya bucket'ı (SSE-S3).
- Telegram webhook: REST API Gateway + ARM64 Lambda (`yusufclaw-telegram-webhook`).
- **Web UI + AG-UI backend:** `yusufclaw-web` Lambda konteyneri (Lambda Web Adapter, `RESPONSE_STREAM`) + Function URL (HTTPS). ALB/NAT/CloudFront yok, sıfıra ölçeklenir.
  - Kimlik doğrulama: her API çağrısında `Authorization: Bearer <token>`. Token dağıtımda üretilir ve **yalnızca** Secrets Manager'da `yusufclaw/web/auth-token` adıyla saklanır. Giriş: URL'yi aç, "Access token" alanına secret değerini yapıştır (`aws secretsmanager get-secret-value --secret-id yusufclaw/web/auth-token --profile yusufclaw --query SecretString --output text`).
  - Model: `us.anthropic.claude-haiku-4-5-20251001-v1:0` (Bedrock, rol kimlik bilgileri; `BedrockProvider` profil yoksa varsayılan kimliğe düşer).
  - Kısıt: bellek `/tmp/memory.sqlite` (soğuk başlatmada sıfırlanır); onay kartı (YC-05) bellek içi olduğundan, Lambda'da onay isteği farklı bir örneğe düşebilir → onay gerektiren araçlar güvenilir çalışmayabilir (çözüm: onayları DynamoDB'ye taşımak).
- ECS Fargate cluster + task definition (heavy jobs), 0 NAT, public subnet; yalnızca RunTask ile çalışır (boşta $0).
- AWS Budget (50 USD) → SNS topic `BudgetAlertTopicArn` (e-posta aboneliği yok; istenirse sonradan abone olunabilir).

### Tahmini aylık maliyet
- Boşta: ~1 USD (Secrets Manager 0.40, ECR imajı ~0.10, S3/DynamoDB/Logs kuruşlar; VPC/NAT/ALB/sabit compute yok).
- Kullanım: Haiku 4.5 ~1 USD/M girdi, 5 USD/M çıktı token → günde ~100 mesajla ~10–20 USD. Fargate işi ~0.05 USD/saat. 50 USD bütçe içinde.

### Dağıtımı tamamlamak için (tek seferlik, root)
```bash
aws login   # root/default oturumu
cd ~/projects/wt/yc-deploy/infra_py
cdk bootstrap aws://478170046831/us-east-1 --profile default --tags Project=yusufclaw
aws iam put-user-policy --profile default --user-name yusufclaw-agent --policy-name cdk-assume \
  --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"sts:AssumeRole","Resource":"arn:aws:iam::478170046831:role/cdk-hnb659fds-*"}]}'
cdk deploy --all --require-approval never --profile yusufclaw
```

### Kaldırma (teardown)
```bash
cd infra_py && cdk destroy --all --force --profile yusufclaw
# DynamoDB tablosu ve S3 bucket RETAIN politikasındadır; gerekirse elle silin.
```
---

## 27. Python Çok Kanallı İletişim ve Ses Katmanı (WhatsApp & Agnostic Voice) — `py-channels` Dalı

- Yeni `src/yusufclaw/gateway/whatsapp.py` adaptörü: Meta WhatsApp Business Cloud API entegrasyonu; `hub.mode`, `hub.verify_token`, `hub.challenge` GET doğrulama el sıkışması, `X-Hub-Signature-256` HMAC-SHA256 imza denetimi, telefon numarası normalizasyonu ve sahip izin listesi (`WHATSAPP_ALLOWED_NUMBERS`). Yetkisiz göndericiler MessageBus ve ajana ulaşmadan erken aşamada reddedilir.
- YC-05 Onay Paritesi: Eylem kartları (`ActionCard`) WhatsApp etkileşimli yanıt düğmeleri (`interactive reply buttons`: `approve:<id>:<nonce>`, `deny:<id>:<nonce>`, `details:<id>:<nonce>`) olarak iletilir. Nonce eşleşmesi, argüman tahrifatı koruması, 300 saniye zaman aşımı ve replay saldırı engellemesi Telegram ile birebir aynı `ApprovalProvider` sözleşmesiyle korunur.
- Yeni `src/yusufclaw/voice/` paketi: Sağlayıcıdan bağımsız `SpeechToText` ve `TextToSpeech` protokolleri (`types.py`), deterministik testler için `FakeSTT` ve `FakeTTS` (`fake.py`), bulut OpenAI Whisper/TTS (`openai_voice.py`), yerel iş parçacıklı `faster-whisper` (`whisper_voice.py`) ve AWS Transcribe/Polly (`aws_voice.py`) sağlayıcıları ile `get_stt_provider` / `get_tts_provider` fabrikası.
- Telegram Ses Entegrasyonu (`src/yusufclaw/gateway/telegram.py`): Sesli mesajlar (`TelegramVoice`) Bot API üzerinden indirilip STT protokolü ile metne deşifre edilir ve MessageBus'a iletilir; yanıtlar opsiyonel olarak TTS sentezi ile sesli mesaj (`sendVoice`) olarak döndürülebilir.
- Kanal Kayıt ve Yapılandırma Kancası (`src/yusufclaw/gateway/channels.py`): `ChannelRegistry` ve `setup_channels` ile kanallar ve ses katmanı Pydantic ayarları üzerinden bağlanır. Gerekli kimlik bilgisi veya izin listesi olmayan kanallar kesin olarak kapalı kalır.
- Doğrulama: `uv run pytest -q && uv run ruff check . && uv run mypy src` → **350 passed, 1 skipped, 1 xfailed**; ruff temiz; strict mypy temiz (109 kaynak dosyasında 0 hata). Çevrimdışı test süitleri `tests/python/test_whatsapp_gateway.py`, `tests/python/test_voice.py`, `tests/python/test_channels_wiring.py` (26 yeni test) canlı ağ veya AWS gerektirmez. Kullanım ve kılavuz: `docs/PY-CHANNELS.md`. `gateway/web.py`, `gateway/web_page.py`, `gateway/approval.py` iç mantığı, `a2a/`, `writers/`, `memory/`, `sandbox/`, `evals/`, queue store ve roller değiştirilmedi. AWS kullanılmadı; push yapılmadı. Birleştirmede §27 olarak numaralandı; mevcut bölümler yeniden numaralandırılmadı.

---

## 28. Python Web Gateway AG-UI 1.0 ve UX Sprint 1 (UX-01..UX-06) — `py-ux-s1` Dalı

- **Kapsam ve Amaç**: `docs/UX-SPEC.md` §7 Sprint 1 kapsamındaki UX-01 ile UX-06 arasındaki tüm maddelerin FastAPI tabanlı web ağ geçidi (`src/yusufclaw/gateway/`) üzerinde eksiksiz ve geriye dönük uyumlu olarak hayata geçirilmesi (P2-03, AG-UI 1.0).
- **UX-01 (AG-UI 1.0 Protokol Uyum Boşlukları)**: `src/yusufclaw/gateway/models.py` içinde `AgUiEnvelope` kanonik zarf modeli (`eventId`, `runId`, `sessionId`, `agentId`, `timestamp`, `sequence`, `eventType`, `payload`). `POST /api/chat/stream` üzerinden tipli `run.acknowledged`, `run.milestone`, `message.delta`, `message.thought`, `run.completed` SSE olayları; eski istemciler için `RunStarted`, `progress`, `TextMessageStart`, `TextMessageContent`, `TextMessageEnd`, `RunFinished` olayları korunarak çift uyumluluk sağlandı.
- **UX-02 (Adlandırılmış Uzman Ajanlar Kenar Çubuğu ve Oturum Yönlendirici)**: `GET /api/agents` uç noktası ile rol kataloğu personelleri (`orchestrator`, `coder`, `researcher`, `ops` vb.) servis edilir. Kenar çubuğundan ajan seçimi; `web:{userId}:{agentId}:{conversationId}` bileşik anahtarıyla çoklu ajan konuşma hafızası izolasyonu sağlandı.
- **UX-03 (Anlık Bilişsel Onay ve Kilometre Taşı Adımlayıcısı)**: Kullanıcı mesajı gönderildiği anda (<300ms algısal gecikme eşiği) niyet ve tahmini adım sayısı içeren `run.acknowledged` ve ilk `run.milestone` istemciye basılır; uzun süren asenkron araç döngüleri `status="running"` ve `status="completed"` adımlayıcısıyla canlı izlenir.
- **UX-04 (Satır İçi Karar ve Soru Widget'ları)**: Çoktan seçmeli veya tekil seçenekli `widget.question` SSE olayları ve istemci buton çipleri; `POST /api/actions/resolve-widget` ile asenkron turn geleceği (future) çözümlenir, mükerrer yanıtlar HTTP 400 ile engellenir.
- **UX-05 (Doğrulanabilir Eylem Kartları ve Telegram Çapraz Kanal Eşitliği)**: Geri alınabilirlik, güvenlik lekesi (`taint`), canlı argüman önizlemesi ve 5 dakikalık TTL geri sayımına sahip `widget.approval_card` bileşeni. Tahrif edilmiş argümanlar fail-closed reddedilir. `web_approve` içindeki asenkron izleyici (`_watch_provider`) sayesinde Telegram botu veya CLI üzerinden onaylanan kartlar web akışını eşzamanlı olarak çözer. `POST /api/actions/resolve-approval`, `POST /api/chat/approval`, `POST /api/approval` uç noktaları.
- **UX-06 (Maskeli Kimlik Bilgisi Talebi ve Yerel Credential Broker)**: `widget.secret_request` olayı ve web modal diyaloğu. `src/yusufclaw/gateway/credentials.py` içinde iş parçacığı güvenli `CredentialBroker`: gizli değerler yalnızca bellekte/yerel anahtarlıkta saklanır, 12 karakterlik SHA-256 parmak izi üretilir ve model bağlamına zararsız sistem hapı (`[Key configured: <name> (sha256: <fp>)]`) olarak sunulur. Gizli anahtarlar asla SQLite `turns`, `memory_records` veya loglara yazılmaz. `POST /api/actions/secret-submit` ve `GET /api/credentials` (yalnızca metadata/parmak izi) uç noktaları.
- **Web UI & Güvenlik Kısıtları (`src/yusufclaw/gateway/web_page.py`)**: Saf vanilla HTML5/CSS3/JS, harici kütüphane bağımlılığı yok. Sıkı CSP uyumu: `innerHTML` yok, `localStorage` yok, `sessionStorage` yok; tüm DOM metinleri güvenli biçimde `textContent` ile oluşturulur.
- **Korunan Modüller ve Mimari Sınırlar**: `a2a/`, `writers/`, `memory/`, `sandbox/`, `evals/`, kuyruk motoru içleri, rol YAML'ları ve `orchestration/subagent.py` dosyalarına kesinlikle dokunulmamıştır. `agent.py` içinde yalnızca `ask_question` geri çağrısı için minimal ekleme yapılmıştır. Birleştirmede §28 olarak numaralandı; §§25–27 (a2a, deploy, channels) korunmuştur.
- **Doğrulama ve Testler**:
  - `tests/python/test_ux_sprint1.py`: UX-01'den UX-06'ya kadar 6 kapsamlı çevrimdışı test senaryosu.
  - `uv run pytest -q`: **311 passed, 1 skipped, 1 xfailed**.
  - `uv run ruff check .`: Sıfır hata (temiz).
  - `uv run mypy src`: 94 kaynak dosyasında sıfır hata (strict mypy temiz).
- **Dokümantasyon**: Detaylı kullanım, protokol şemaları ve mimari kararlar `docs/PY-UX-S1.md` dosyasında belgelenmiştir.

