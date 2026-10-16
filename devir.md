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

## 4. Sonraki Ajan İçin Notlar
- Bellek altyapısı `packages/core` içinde tamamen hazır ve `packages/gateway` tarafından doğrudan kullanılabilir durumdadır.
- AWS altyapısına henüz deploy yapılmamıştır (`packages/infra` kuralları korunmuştur).
- Git dalı `phase3-memory` üzerindedir; uzak depoya push yapılmamalıdır.
