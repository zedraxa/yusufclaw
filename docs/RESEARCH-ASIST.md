# YusufClaw Derin Araştırma Raporu (2025–2026 literatürü)

**Proje:** YusufClaw — her yerden komut verilebilen açık kaynak kişisel AI ajanı ([github.com/zedraxa/yusufclaw](https://github.com/zedraxa/yusufclaw))
**Tarih:** 16 Ekim 2026 (Europe/Istanbul)
**Kapsam:** ajan mimarisi ve alt ajanlar, bellek dinamikleri, araç kullanımı/MCP/computer use/skill/rutinler, çok kanallı arayüz ve onay akışları, güvenlik, değerlendirme
**Önceki çalışma:** `docs/RESEARCH.md` ve `docs/DESIGN-RECOMMENDATIONS.md` (Codex araştırması, aynı tarih)

---

## 0. Yöntem ve bu raporun önceki araştırmadan farkı

**Ne okundu.** Depo `main` dalından klonlandı. `SPEC.md`, `PROGRESS.md`, `devir.md`, `docs/RESEARCH.md`, `docs/DESIGN-RECOMMENDATIONS.md` ile `packages/core`, `packages/tools`, `packages/models` ve `packages/gateway` kaynak kodu incelendi. Kodla ilgili tespitler (bkz. §1.3) **statik kod okumasına** dayanıyor. Hiçbir saldırı çalıştırılmadı ve canlı bir sistemde denenmedi.

**Kaynak doğrulama.** Rapordaki her arXiv kaynağının `https://arxiv.org/abs/<id>` sayfası 16 Ekim 2026'da tek tek indirildi. HTTP 200 dönmesi ve sayfadaki `citation_title`/`citation_author`/`citation_date` meta alanlarının okunması şart koşuldu. Başlıklar, yazarlar ve tarihler kaynakçaya bu meta verilerden **otomatik** aktarıldı. Sayısal bulgular yalnızca bu sayfalardaki özetlerden (abstract) alındı. Bir kaç yerde makalenin HTML tam metni kullanıldı ve bu yerler ayrıca belirtildi. Arama sonuçlarında geçip doğrulanamayan veya başlığı beklenenden farklı çıkan kaynaklar ya düzeltildi ya da çıkarıldı. Örneğin arXiv:2508.03341'in gerçek başlığı *"What Deserves Memory: Adaptive Memory Distillation for LLM Agents"* (Nemori), arXiv:2603.27517'nin güncel başlığı ise *"A Security Analysis of the OpenClaw AI Agent Framework"*. arXiv dışı dört kaynağın (iki Simon Willison yazısı, Anthropic mühendislik yazısı, Agent Skills spesifikasyonu) sayfaları da açılıp başlıkları kontrol edildi.

**Uyarı.** arXiv ön baskılarının çoğu hakem değerlendirmesinden geçmemiştir. Bildirilen yüzdeler o makalenin model/ortam/bütçe koşullarına aittir ve YusufClaw'da aynı sonucun alınacağı anlamına gelmez. Öncelikler (P0/P1/P2) ve TypeScript taslakları bu raporun **mühendislik önerileridir**, makalelerin sonucu değildir.

**Önceki araştırmanın (RESEARCH.md) kapsamı.** Codex raporu esas olarak 2022–2024 temellerine dayanıyor: ReAct, Reflexion, Plan-and-Solve, ReWOO, AutoGen, MemGPT, Generative Agents, Toolformer, Gorilla, Voyager, InjecAgent, AgentDojo, GAIA, τ-bench, LoCoMo, LongMemEval, WebArena, OSWorld, SWE-bench. 2025 kaynağı olarak yalnızca A-MEM, Mem0 ve Zep var. Mühendislik tavsiyeleri sağlam: tek sahipli döngü, onay bağlama (args hash/nonce), FTS5 önce, unutma türleri, Telegram long polling, Node 24 önerisi. Bu rapor bunları tekrar etmiyor. Odak, **2025–2026'da çıkan ve RESEARCH.md'de hiç geçmeyen** bulgular. En önemlileri:

| # | RESEARCH.md'de olmayan yeni bulgu | YusufClaw'a etkisi |
|---|---|---|
| 1 | **OpenClaw'ı doğrudan hedef alan 2026 güvenlik literatürü** (CIK taksonomisi, PASB, ClawSafety, 470 advisory analizi, OpenClaw türevleri değerlendirmesi) | YusufClaw aynı ürün sınıfında. Kalıcı durumun (yetenek/kimlik/bilgi) zehirlenmesi saldırı başarısını ~3 kat artırıyor. Komut allowlist'inin sözcüksel ayrıştırmaya dayanması kanıtlanmış bir açık sınıfı. |
| 2 | **Yetenek/IFC tabanlı savunmaların olgunlaşması:** CaMeL → Operationalizing CaMeL, CaMeLoT (statik plan doğrulama), multi-CaMeL (alt ajanlarda bileşim *bozuluyor*), Progent, FIDES, DRIFT, AuthGraph, SkillGuard | "Model öneriyor, politika karar veriyor" ilkesi artık uygulanabilir somut desenlere sahip. Alt ajan eklenecekse veri/talimat kanalları ayrılmalı. |
| 3 | **Uyarlanabilir saldırılar** 12 savunmayı çoğunda %90 üzeri başarıyla aşıyor | Sınıflandırıcı/prompt tabanlı savunma tek başına kapı olamaz. |
| 4 | **Bellek zehirlenmesi** (MPBench, GhostWriter, TMA-NM, SMSR): içerik ve köken (lineage) tabanlı savunmalar "aklama" (laundering) altında sağlam değil | Bellek yazımında **yazma anında köken bağlama** gerekli. Ajanın kendi özetleri güvenilir sayılmamalı. |
| 5 | **Skill tedarik zinciri:** 98.380 skill'de 157 doğrulanmış kötü amaçlı skill. SKILL.md metni seçim ve keşfi manipüle edebiliyor. Skill dosyaları en tehlikeli enjeksiyon kanalı (ClawSafety). | YusufClaw şu an `skills/` içeriğini doğrudan sistem promptuna ekliyor. Skill'ler imza/pin ve yetki manifestosu ile yönetilmeli. |
| 6 | **MCP'ye özgü saldırılar:** araç-açıklaması zehirlenmesi (MCPTox, MSB, MCP-TDP), protokol düzeyi zayıflıklar | MCP istemcisi açıklama pinleme, ad alanı ve yetenek kısıtı olmadan açılmamalı. |
| 7 | **Onay akışlarının insan faktörü çalışmaları:** onay penceresi ajan tarafından anlatıldığında sahtelenebilir (Lies-in-the-Loop / Consent Integrity, Verifiable Action Card). Kullanıcı politikaları beklenenden *zayıf* koruyor. Plan onayı çalışma anı dikkatini azaltıyor. | Telegram/web onay kartı, ajanın metninden değil, gerçek eylem tanımlayıcısından güvenilir şekilde üretilmeli. |
| 8 | **Bellek araştırmasının 2025–26 dalgası:** MemoryOS, MemOS, MIRIX, LightMem, Nemori, RMM, ReasoningBank, Memp, sleep-time compute, Mem-α/Memory-R1 (RL), FadeMem, TrustMem; çizge belleğin düz vektörü *geçemediği* kontrollü sonuç | Konsolidasyon "uyku zamanı" işine taşınmalı. Başarısızlıklardan da prosedürel ders çıkarılmalı. Çizge veritabanı ertelenmeli. |
| 9 | **Unutma değerlendirmesi:** Bemula'da hiçbir sistem silme isteklerinin %1,5'inden fazlasını uçtan uca geçemiyor. Memora/FAMA eskimiş belleği cezalandırıyor. | `/forget` için kabul testleri ilk günden yazılmalı. |
| 10 | **Alt ajan/paralellik ampirikleri:** MAST (14 hata modu), dinamik eşzamanlılıkta 1,41–3,31× token, CAID (worktree + merge + test), NP-Bench, AOrchestra 4'lüsü | Alt ajanlar *bağlam izolasyonu ve paralel okuma* için faydalı, yazma işlerinde ise izole çalışma alanı ve entegrasyon testi şart. |
| 11 | **Bağlam sıkıştırma:** daha az token her zaman daha hızlı/ucuz değil. Deterministik, yapı-farkındalıklı tahliye (CWL) ve durum koşullu sıkıştırma | Bugünkü "son 20 tur" penceresi yerine olay-tipli tahliye politikası. |
| 12 | **Kişisel ajan benchmark'ları:** ClawBench, RealClawBench, π-Bench (proaktiflik), Gaia2/ARE (asenkron, zaman kısıtlı), OSWorld 2.0, OSGuard, Full-Duplex-Bench-v3 (ses) | YusufClaw'ın kendi değerlendirme kümesi bu ölçütlerle hizalanabilir. |

---

## Yönetici özeti

1. YusufClaw'ın Phase 1–2 kodu, temiz ayrılmış paketleri, sınırlı döngüsü, Ajv doğrulaması, Telegram allowlist'i ve testleri ile iyi bir temel. Ancak varsayılan araç seti, Willison'ın "ölümcül üçlü" dediği koşulu **onaysız** olarak bir araya getiriyor: özel veri okuma, güvenilmeyen içerik ve dış iletişim aynı anda mümkün. Bu, P0 kapsamında giderilmesi gereken bir risk.
2. 2026'da OpenClaw üzerinde yapılan gerçek dünya çalışmaları, kişisel ajanlardaki açıkların modelden çok **mimariden** kaynaklandığını gösteriyor. Kalıcı durum zehirlenmesi saldırı başarısını %24,6'dan %64–74'e çıkarıyor. Sözcüksel komut allowlist'leri atlatılabiliyor.
3. Güvenlik için önerilen çekirdek, CaMeL/Progent/FIDES çizgisinde **deterministik bir politika motoru**. Bu motor kaynak etiketli değerleri (taint) izler, "kirlendikten sonra yetki daraltır" ve yetki genişletmesini yalnızca onaya bağlar. Prompt tabanlı savunmalar sadece ek bir katman olabilir.
4. Bellekte öneri, SQLite üzerinde **olay → bölüm → aday iddia → doğrulanmış iddia → prosedür** hattı. Konsolidasyon "uyku zamanı" rutini olarak çalışır. Her yazımda köken ve güven etiketi tutulur. Çizge belleği şimdilik ertelenmeli.
5. Alt ajanlar P1'de yalnızca salt-okur araştırma ve özetleme için, ayrı veri kanalı ve bütçe ile açılmalı. Yazma yapan alt ajanlar (agy/codex) P2'de, git worktree izolasyonu ve test kapısı ile gelmeli.
6. MCP ve skill'ler bir **tedarik zinciri** sorunu olarak ele alınmalı: pinleme, açıklama hash'i, yetenek manifestosu, ilk kullanımda onay.
7. Onay kartları, ajanın anlatımından değil, gerçek eylemden ve güvenilir bir oluşturucudan üretilmeli ("What You Approve Is What Executes").
8. Değerlendirmede ClawBench/RealClawBench/π-Bench/Bemula tarzı küçük ama gerçekçi bir iç küme, AgentDojo/MCPTox/ClawSafety tarzı saldırı fikstürleriyle birlikte CI'a girmeli.

---
## 1. YusufClaw'ın bugünkü durumu (kod okuması)

### 1.1 Mimari özet

| Paket | Bugün ne var (main, 16.10.2026) |
|---|---|
| `core` | `Agent`: oturum başına sıralı kuyruk, `maxSteps` (8) sınırı, adım başına en çok 16 araç çağrısı, Ajv ile argüman doğrulama, `requiresApproval` (boolean veya argümana bağlı fonksiyon), araç çıktısının 32.000 karakterle kesilmesi. `SqliteMemory`: `turns(session, request_id, messages, reply)` ve `state` tabloları, WAL; geçmiş "son N tur" ile seçiliyor. `skills.ts`: `skills/` altındaki `.md` dosyaları sistem promptuna "Trusted local skill instructions" başlığıyla ekleniyor. `routines.ts`: UTC 5 alanlı cron, SQLite tamamlanma işaretleri. |
| `models` | Bedrock Converse, Ollama, OpenAI-uyumlu Chat Completions. Akış (stream) yok, `complete()` tek yanıt döndürüyor. |
| `tools` | `shell` (`/bin/sh -c`, binary allowlist, regex ile "tehlikeli komut" sınıflandırması, cwd sınırlaması, temizlenmiş env), `file_read/list` (onaysız), `file_write` (onaylı), `web_fetch` (onaysız GET/HEAD, yalnızca metadata IP'leri engelli), `browser` (Playwright, onaysız), `delegate_agent` (agy/codex, varsayılan kapalı, onaylı). |
| `gateway` | CLI (etkileşimli onay "yes"), Telegram long polling (özel sohbet + kullanıcı allowlist; onay gerektiren eylemler **reddediliyor**), token korumalı web arayüzü, rutin zamanlayıcı. |
| `infra` | CDK taslağı (DynamoDB, S3, Fargate heavy jobs, Telegram webhook Lambda). Henüz dağıtılmadı. |

### 1.2 İyi olanlar

Döngü sınırlı. Araç kimlikleri tekrar kontrol ediliyor. Son adımda araç çalıştırılmıyor. Telegram'da onay gerektiren eylem güvenli tarafta kalıp reddediliyor. Shell alt sürecine yalnızca `PATH/HOME/USER/LANG` aktarılıyor, dolayısıyla AWS/Telegram anahtarları ortam değişkeni yoluyla sızmıyor. Dosya araçları symlink kaçışını `realpath` ile engelliyor. Bunlar RESEARCH.md'deki P0 ilkelerinin önemli kısmını karşılıyor.

### 1.3 Yeni literatür ışığında kritik riskler (statik inceleme)

1. **"Ölümcül üçlü" varsayılan olarak açık.** Willison özel veriye erişim, güvenilmeyen içerik ve dışarıya iletişim yeteneklerinin bir arada bulunmasını prompt injection için yeterli koşul olarak tanımlıyor ([Willison, 2025](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/)). YusufClaw'da `web_fetch`/`browser` güvenilmeyen içerik getiriyor (onaysız). `file_read` ve "salt-okur" sayılan `cat/head/grep` komutları onaysız. `web_fetch` ise URL sorgu dizesi üzerinden veri dışarı taşıyabilen onaysız bir GET kanalı. Varsayılan `WORKSPACE_DIR` süreç çalışma dizini, yani depo kökü. Burada `.env` ve `.data/memory.sqlite` bulunuyor. Sonuç olarak zararlı bir web sayfası, tek bir onay sorusu çıkmadan "`.env`'i oku, sonra `https://saldirgan/?q=...` adresini getir" zincirini tetikleyebilir. Bu bir **çıkarım**. Test edilmedi, ancak araçların `requiresApproval` değerleri ve varsayılanlar bu yolu kapatmıyor. ClawSafety ve OpenClaw güvenlik analizleri, aynı sınıftaki ajanlarda bu tür zincirlerin pratikte başarılı olduğunu gösteriyor ([ClawSafety](https://arxiv.org/abs/2604.01438), [OpenClaw güvenlik analizi](https://arxiv.org/abs/2603.27517)).
2. **Sözcüksel komut sınıflandırması.** `isDangerousCommand` ve `extractCommandBinaries` komutu regex ile bölüyor, sonra `sh -c` ile çalıştırıyor. 470 OpenClaw advisory'sini inceleyen çalışma, tam olarak bu tasarımın, yani komut kimliğinin sözcüksel ayrıştırmayla çıkarılabileceği "kapalı dünya" varsayımının, satır devamı, busybox çoğullama ve GNU seçenek kısaltması gibi tekniklerle bozulduğunu raporluyor ([arXiv:2603.27517](https://arxiv.org/abs/2603.27517)). Ayrıca argümanlardaki yollar sınırlanmıyor, yalnızca `cwd` sınırlanıyor: `cat ../x` veya mutlak yollar onaysız çalışabilir.
3. **SSRF kapsamı dar.** Yalnızca bulut metadata adresleri engelleniyor. `127.0.0.1:11434` (Ollama), `127.0.0.1:3000` (YusufClaw web), RFC1918 ağları ve yönlendirme sonrası hedefler açık.
4. **Skill'ler "güvenilir" etiketiyle sistem promptunda.** ClawSafety'de en tehlikeli enjeksiyon vektörü skill dosyaları çıktı, e-posta ve web içeriğinden daha tehlikeli. Skill ekosistemi çalışmaları da gerçek kötü amaçlı skill'ler belgeledi ([arXiv:2604.01438](https://arxiv.org/abs/2604.01438), [arXiv:2602.06547](https://arxiv.org/abs/2602.06547)). Bugün `skills/` boş ve yerel olduğu için risk düşük. Ancak skill paylaşımı/indirme eklenirse yüksek bir risk olur.
5. **Bellek kökensiz.** `turns.messages` araç çıktılarını da içeriyor ve bunlar sonraki turlarda geçmiş olarak geri yükleniyor. Kalıcı bir bilgi katmanı eklendiğinde araç çıktısından türeyen bilgiler, köken etiketi olmadan "kullanıcı bilgisi" gibi davranabilir. Bu, MPBench/GhostWriter'ın tarif ettiği yazma kanalı ([arXiv:2606.04329](https://arxiv.org/abs/2606.04329), [arXiv:2607.06595](https://arxiv.org/abs/2607.06595)).

**Hızlı düzeltmeler (P0, birkaç gün):** (a) varsayılan `WORKSPACE_DIR`'ı `.data/workspace` gibi ayrı bir dizine taşımak ve `.env*`, `*.sqlite`, `.git/config`, `~/.aws` için mutlak bir okuma reddi koymak; (b) güvenilmeyen içerik oturuma girdikten sonra `web_fetch`'i "yalnızca kullanıcının verdiği/izin listesindeki alan adları" ile sınırlamak veya onaya bağlamak (bkz. §6 taint modeli); (c) özel/loopback/link-local IP'leri DNS çözümlemesi ve her yönlendirme sonrasında engellemek; (d) shell için "onaysız" listeyi argv tabanlı yapılandırılmış komutlara indirmek (`sh -c` yalnızca sandbox içinde ve onayla).

---
## 2. Ajan mimarileri ve orkestrasyon (alt ajanlar dahil)

### 2.1 Ana bulgular

**Çok ajanlı sistemler neden başarısız oluyor?** MAST çalışması 7 popüler çok ajanlı çerçeveden 1.600'den fazla izi etiketleyerek 14 hata modu belirledi ve bunları üç kümede topladı: sistem tasarımı sorunları, ajanlar arası uyumsuzluk, görev doğrulama. Yazarlar ayrıca çok ajanlı sistemlerin popüler benchmark'lardaki kazancının "çoğu zaman minimal" olduğunu not ediyor ([Cemri vd., arXiv:2503.13657](https://arxiv.org/abs/2503.13657)). RESEARCH.md alt ajanları P2'ye koyarken AutoGen'e dayanıyordu. MAST bu ertelemeyi ampirik olarak destekliyor.

**Alt ajanlar ne zaman işe yarıyor?** Anthropic'in üretim deneyimi, araştırma tipi "genişlik öncelikli" sorgularda orkestratör–işçi deseninin tek ajana göre belirgin kazanç sağladığını bildiriyor. Aynı yazıya göre BrowseComp'taki performans varyansının %80'i yalnızca token kullanımıyla açıklanıyor. Ajanlar sohbete göre yaklaşık 4×, çok ajanlı sistemler yaklaşık 15× token harcıyor. Yazı, kodlama gibi gerçekten paralelleştirilebilir alt görevi az olan işlerin bu desene daha az uyduğunu da belirtiyor ([Anthropic, 2025](https://www.anthropic.com/engineering/multi-agent-research-system)). Bunlar şirket içi değerlendirmeler, bağımsız olarak tekrarlanmadı. 2026'da Codex, Claude Code ve Kimi Code üzerinde yapılan eşleştirilmiş deneyler (354 görev, 2.124 çalıştırma) dinamik eşzamanlılığın uzun ufuklu görevlerde fayda sağladığını, sınırlı görevlerde ise çoğunlukla az veya negatif etki yaptığını gösterdi. Token kullanımı sıralı çalışmanın 1,41–3,31 katına çıkıyor (makalenin HTML tam metninden). Çalışma 13 eşzamanlılığa özgü hata modu tanımlıyor ([arXiv:2610.10263](https://arxiv.org/abs/2610.10263)).

**Yazma yapan paralel ajanlar için mühendislik ilkeleri.** CAID merkezi delegasyon, asenkron yürütme ve izole çalışma alanı (git worktree/commit/merge) ile test tabanlı entegrasyonu birleştiriyor. Tek ajana göre PaperBench'te %26,7, Commit0'da %14,3 mutlak iyileşme raporluyor ([Geng & Neubig, arXiv:2603.21489](https://arxiv.org/abs/2603.21489)). NP-Bench çakışmaları reaktif tespit yerine önceden kapsam bölümleme ve üretici→tüketici sırasıyla planlıyor: temiz entegrasyon 1/9'dan 9/9 senaryoya çıkıyor, birleşme çakışması 13'ten 0'a iniyor ([arXiv:2610.07261](https://arxiv.org/abs/2610.07261)). Co-Coder bağımlılık grafiği bölümlemesiyle %14'e varan geçme oranı artışı, 2,10× hız ve %35'e varan maliyet düşüşü bildiriyor ([arXiv:2606.00953](https://arxiv.org/abs/2606.00953)).

**Alt ajanı bir "tarif" olarak tanımlamak.** AOrchestra her ajanı *(Talimat, Bağlam, Araçlar, Model)* dörtlüsü olarak modelliyor. Orkestratör her adımda yalnızca göreve ilgili bağlamı süzüp araç ve model seçerek alt ajanı anında oluşturuyor. GAIA/SWE-Bench/Terminal-Bench'te en güçlü taban çizgisine göre %16,28 göreli iyileşme raporluyor ([arXiv:2602.03786](https://arxiv.org/abs/2602.03786)). Bu soyutlama YusufClaw'ın `delegate` aracını genelleştirmek için doğrudan kullanılabilir.

**Heterojen model yönlendirme.** NVIDIA'nın pozisyon makalesi, ajan sistemlerindeki çağrıların çoğunun dar ve tekrarlı olduğunu, bu yüzden küçük modellerin (SLM) bu çağrılar için yeterli ve daha ekonomik olduğunu savunuyor. Genel sohbet gerektiğinde heterojen (farklı modelleri birleştiren) sistemler öneriyor ([arXiv:2506.02153](https://arxiv.org/abs/2506.02153)). YusufClaw'da Ollama'nın konumu tam bu: sınıflandırma, özetleme, bellek çıkarımı gibi alt görevlerde yerel model, plan ve araç seçiminde Bedrock.

**Bağlam yönetimi artık ayrı bir mimari bileşen.** 65 sıkıştırma politikası ve yaklaşık 35.000 çalıştırmalık sistematik çalışma şunu buldu: daha az token her zaman daha hızlı veya ucuz değil. Terminal-Bench'te token'ın yaklaşık üçte birini kullanan politikalar %20–80 daha uzun sürebiliyor ([arXiv:2609.32961](https://arxiv.org/abs/2609.32961)). CWL, özetleme yerine ajanın yörüngesini tipli ve bağımlılık bağlantılı "bölümler" olarak işaretliyor, sonra LLM kullanmayan deterministik bir politikayla etkisi ortama zaten kalıcı yazılmış eylem bölümlerini önce tahliye ediyor ([arXiv:2606.11213](https://arxiv.org/abs/2606.11213)). StateComp "ne zaman sıkıştırmak güvenli" sorusunu öğreniyor ve WorkBuddyBench'te toplam token'ı %52,27 azaltıyor ([arXiv:2609.27298](https://arxiv.org/abs/2609.27298)). SelfCompact, modele bir sıkıştırma aracı ve ne zaman tetiklenip ne zaman bastırılacağını söyleyen bir rubrik veriyor ([arXiv:2606.23525](https://arxiv.org/abs/2606.23525)). Paralel sıkıştırma ise özet hacmini öngörülebilir kılıyor ([arXiv:2605.23296](https://arxiv.org/abs/2605.23296)). ACE, bağlamı "gelişen oyun kitabı" olarak ele alıyor ve yinelemeli yeniden yazımın ayrıntıları aşındırdığı **bağlam çöküşü** (context collapse) ile **kısalık yanlılığı** (brevity bias) sorunlarını adlandırıyor. Artımlı ve yapılandırılmış güncellemelerle ajan görevlerinde +%10,6 bildiriyor ([arXiv:2510.04618](https://arxiv.org/abs/2510.04618)). Dynamic Cheatsheet de test anında biriken stratejilerin kalıcı bellekte tutulmasının etkisini gösteriyor ([arXiv:2504.07952](https://arxiv.org/abs/2504.07952)).

**Kendini değiştiren ajanlar.** Darwin Gödel Machine kendi kodunu değiştirip her değişikliği kodlama benchmark'larında doğrulayarak gelişiyor ([arXiv:2505.22954](https://arxiv.org/abs/2505.22954)). Bu, kişisel bir ajan için güvenlik politikasının ve çekirdeğin ajanın yazma alanı dışında tutulmasının neden gerekli olduğunu gösteren bir sınır örneği.

### 2.2 YusufClaw için tasarım önerisi

**(a) Çekirdeği "durum makinesi + olay günlüğü" yapmak (P0).** Bugün `Agent.run` bir `for` döngüsü ve tamamlanmış turu tek satır olarak kaydediyor. Önerilen yapı: `runs` ve `run_events` tabloları. Her model adımı, araç önerisi, politika kararı ve araç sonucu ayrı bir olay olsun. CWL tarzı tahliye ve çökme sonrası devam bu olaylar üzerinden çalışır.

```ts
// packages/core/src/run.ts (taslak)
type EventKind =
  | 'user_msg' | 'model_step' | 'tool_proposed' | 'policy_decision'
  | 'tool_result' | 'approval_request' | 'approval_resolved' | 'final';
interface RunEvent {
  runId: string; seq: number; kind: EventKind;
  episodeId?: string;          // CWL: tipli bölüm
  dependsOn?: number[];        // tahliye için bağımlılık
  persistedEffect?: boolean;   // etkisi dış dünyaya yazıldı mı (tahliye önceliği)
  labels: Label[];             // §6: köken/güven etiketleri
  payload: unknown; tokens?: number; createdAt: string;
}
```

**(b) Bağlam derleyicisi (P0→P1).** `history(session, 20)` yerine bir `ContextAssembler` gelsin. Kullanıcı turları ve açık görevler korunur. Etkisi kalıcılaşmış araç bölümleri, önce kısa makbuzlarına ("dosya X yazıldı, sha=…") indirgenir, sonra tahliye edilir. LLM özetleme yalnızca son çare olur ve Ollama'da asenkron çalışır. Ölçüt, token sayısı değil, görev başarısı ve uçtan uca süre olmalı ([arXiv:2609.32961](https://arxiv.org/abs/2609.32961)).

**(c) Model yönlendirici (P1).** `models` içinde `RoutedProvider` olsun: `role: 'plan' | 'tool' | 'summarize' | 'extract' | 'classify'`. Varsayılan eşleme `plan/tool → Bedrock`, `summarize/extract/classify → Ollama` (yerelde mümkünse). Gizlilik bayrağı taşıyan oturumlar buluta yükseltilmez (RESEARCH.md ilkesi).

**(d) Alt ajanlar = AOrchestra dörtlüsü + CaMeL veri kanalı (P1 salt-okur, P2 yazma).**

```ts
interface SubagentSpec {
  instruction: string;            // yalnızca GÜVENİLİR kaynaktan (kullanıcı/plan)
  context: DataRef[];             // güvenilmeyen veri: ayrı kanal, talimat değil (multi-CaMeL)
  tools: ToolGrant[];             // ebeveyn yetkilerinin kesişimi, genişletme yok
  model: ModelRole;               // 'summarize' → Ollama vb.
  budget: { steps: number; tokens: number; wallMs: number };
  outputSchema: JSONSchema;       // yapılandırılmış dönüş; serbest metin talimat sayılmaz
  workspace?: { kind: 'worktree'; repo: string; baseRef: string }; // P2 yazma
}
```

Kurallar: derinlik 1 olmalı, en çok 3 paralel salt-okur işçi çalışmalı. Çıktılar Anthropic'in önerdiği gibi dosya/artefakt referansı olarak dönmeli ("telefon oyununu" azaltır). Alt ajan çıktısı ebeveyne **veri** olarak etiketli döner ve ebeveynin talimat kanalına karışmaz. Bunun gerekçesi şu: CaMeL'in garantileri hiyerarşik sistemlerde bileşim yoluyla korunmuyor ([arXiv:2610.05640](https://arxiv.org/abs/2610.05640)). Yazma yapan `agy`/`codex` delegeleri için CAID deseni kullanılmalı: her delege kendi `git worktree`'sinde çalışır, sonuç `pnpm check` ile doğrulanır ve ana dala yalnızca kullanıcı onayıyla birleştirilir. Bütçe telemetrisi MAST kategorilerine göre (tasarım, uyumsuzluk, doğrulama) etiketlenmeli.

**(e) Kendini değiştirme yasağı (P0).** `packages/*`, politika yapılandırması ve `skills/` (yayımlanmış sürümler), ajanın dosya/shell araçlarının yazma kümesinden kesin olarak çıkarılmalı.

**Öncelik:** (a) P0, (b) P0 basit tahliye ve P1 öğrenilmiş/özetli tahliye, (c) P1, (d) salt-okur P1 ve yazma P2, (e) P0.

---
## 3. Bellek dinamikleri

### 3.1 Ana bulgular

**Taksonomi netleşti.** 2025 taraması belleği parametrik ve bağlamsal (yapılı/yapısız) olarak ayırıyor ve altı atomik işlem tanımlıyor: *Konsolidasyon, Güncelleme, İndeksleme, Unutma, Geri getirme, Yoğunlaştırma* ([Du vd., arXiv:2505.00675](https://arxiv.org/abs/2505.00675)). Aralık 2025 tarihli kapsamlı tarama, klasik kısa/uzun süreli ayrımının çağdaş sistemleri açıklamaya yetmediğini, ajan belleğinin RAG ve bağlam mühendisliğinden ayrı tutulması gerektiğini vurguluyor ([Hu vd., arXiv:2512.13564](https://arxiv.org/abs/2512.13564)). İnsan belleğinden ilham alan sınıflandırma da aynı dönemde yayımlandı ([arXiv:2504.15965](https://arxiv.org/abs/2504.15965)). YusufClaw için en kullanışlı çerçeve bu altı işlem. Her biri ayrı bir iş ve ayrı bir test olarak ele alınabilir.

**"Bellek işletim sistemi" dalgası.** MemoryOS kısa/orta/uzun süreli üç katman kullanıyor. Kısadan ortaya geçiş diyalog zinciri tabanlı FIFO, ortadan uzuna geçiş segmentli sayfa organizasyonu ile yapılıyor. LoCoMo'da GPT-4o-mini ile F1'de ortalama %49,11 iyileşme raporluyor ([arXiv:2506.06326](https://arxiv.org/abs/2506.06326)). MemOS belleği yaşam döngüsü yönetimi olan birinci sınıf bir kaynak olarak ele alıyor ([arXiv:2507.03724](https://arxiv.org/abs/2507.03724)). MIRIX altı bellek tipini (Core, Episodic, Semantic, Procedural, Resource, Knowledge Vault) çok ajanlı bir denetleyiciyle yönetiyor ([arXiv:2507.07957](https://arxiv.org/abs/2507.07957)). "Knowledge Vault" kavramı, gizli bilgilerin ayrı ve korumalı bir depoda tutulması fikriyle örtüşüyor.

**Konsolidasyonu çevrim dışına almak.** LightMem Atkinson–Shiffrin modelinden ilham alan üç aşama kullanıyor: duyusal filtreleme/sıkıştırma, konu-farkındalıklı kısa süreli bellek ve "uyku zamanı güncellemesi" ile çevrim dışı uzun süreli konsolidasyon. LongMemEval/LoCoMo'da doğrulukta %7,7/%29,3'e varan artış ve 38×/20,9×'e varan token azalması raporluyor ([arXiv:2510.18866](https://arxiv.org/abs/2510.18866)). Letta ekibinin *sleep-time compute* çalışması, bağlam üzerinde sorgu gelmeden önce düşünüp ön hesaplama yapmanın, aynı doğruluk için test anı hesabını yaklaşık 5× azaltabildiğini gösteriyor ([arXiv:2504.13171](https://arxiv.org/abs/2504.13171)). Bu iki sonuç, YusufClaw'ın zaten var olan rutin zamanlayıcısının "gece konsolidasyonu" için kullanılmasını destekliyor.

**Neyin hatırlanmaya değer olduğu.** Nemori, ham etkileşimleri tutarlı anlatılara (episodic) çeviriyor ve semantik bilgiyi "tahmin hatası" ile, yani modelin önceden bilip tahmin edemediği şeylerle damıtıyor ([arXiv:2508.03341](https://arxiv.org/abs/2508.03341)). SeCom, bellek biriminin ayrıntı düzeyinin (tur/oturum/özet) geri getirme kalitesini belirlediğini gösteriyor ve bölüt (segment) düzeyini öneriyor. Ayrıca istem sıkıştırmanın gürültü giderici olarak çalıştığını raporluyor ([arXiv:2502.05589](https://arxiv.org/abs/2502.05589)). RMM ileriye dönük (oturumlar arası özetleme) ve geriye dönük (geri getirmeyi yanıttaki atıflara göre düzeltme) yansımayı birleştiriyor ([arXiv:2503.08026](https://arxiv.org/abs/2503.08026)).

**Prosedürel bellek: başarısızlıktan da öğrenmek.** ReasoningBank, ajanın kendi değerlendirdiği başarılı **ve başarısız** deneyimlerinden genellenebilir akıl yürütme stratejileri damıtıyor. Yalnızca ham yörüngeleri veya yalnızca başarılı rutinleri saklayan mekanizmalardan daha iyi sonuç veriyor ([arXiv:2509.25140](https://arxiv.org/abs/2509.25140)). Memp yörüngeleri hem adım adım talimatlara hem de üst düzey betik soyutlamalarına dönüştürüyor. İçeriği sürekli güncelleyen, düzelten ve **kullanımdan kaldıran** (deprecate) bir rejimle başarı oranı ve verimlilik artıyor ([arXiv:2508.06433](https://arxiv.org/abs/2508.06433)). Bu, RESEARCH.md'deki "skill = SKILL.md + test" önerisini otomatik aday üretimiyle tamamlıyor.

**Öğrenilmiş bellek yönetimi (RL).** Memory-R1, ADD/UPDATE/DELETE/NOOP işlemlerini öğrenen bir Bellek Yöneticisi ile bir Yanıt Ajanını sonuç odaklı RL ile eğitiyor ([arXiv:2508.19828](https://arxiv.org/abs/2508.19828)). Mem-α da bellek inşasını RL ile öğreniyor ([arXiv:2509.25911](https://arxiv.org/abs/2509.25911)). 2026'da çıkan TrustMem, bellek **geçişlerini** kapsam, koruma ve sadakat açısından değerlendiren bir doğrulayıcı kullanıyor. En güçlü taban çizgisine göre geçiş düzeyinde ihmal, bozulma ve halüsinasyonu sırasıyla %40,1, %79,1 ve %50,0 azaltıyor ([arXiv:2606.25161](https://arxiv.org/abs/2606.25161)). YusufClaw model eğitmeyecek. Ancak ADD/UPDATE/DELETE/NOOP işlem kümesi ve "geçiş doğrulayıcı" fikri, eğitim olmadan da (kural + küçük LLM denetimi) uygulanabilir.

**Unutma.** FadeMem, anlamsal ilgi, erişim sıklığı ve zamansal örüntülerle modüle edilen üstel bozunma kullanıyor ve depolamada %45 azalma raporluyor ([arXiv:2601.18642](https://arxiv.org/abs/2601.18642)). SCM, NREM/REM esinli uyku evreli konsolidasyonu ve değer tabanlı unutmayı bir "araştırma önizlemesi" olarak sunuyor ([arXiv:2604.20943](https://arxiv.org/abs/2604.20943)). Tek yazarlı ve küçük ölçekli bir çalışma olduğu için yalnızca fikir kaynağı olarak alınmalı. En öğretici sonuç kontrollü bir 2026 çalışmasından geliyor: LongMemEval'de, eşit aday bütçesinde **bilgi çizgisi belleği düz vektör tabanını geçemiyor** (token F1 0,417'ye karşı 0,468). Özellikle önceki bir asistan turunu birebir hatırlamayı gerektiren sorularda yargılanan doğruluk 0,911'den 0,607'ye düşüyor. Buna karşılık seçici unutma modülü düğümlerin %9,8'ini siliyor ve F1 pratikte değişmiyor ([arXiv:2608.28978](https://arxiv.org/abs/2608.28978)). HippoRAG 2 de yapılandırılmış RAG'in temel olgusal bellek görevlerinde standart RAG'in altına düşebildiğini ve bunu düzeltmenin gerektiğini gösteriyor ([arXiv:2502.14802](https://arxiv.org/abs/2502.14802)). Sonuç: Zep/Graphiti tarzı çizge belleği **ölçülmüş bir ihtiyaç olmadan** eklenmemeli. RESEARCH.md'deki P2 kararı doğru ve yeni kanıtla daha da güçleniyor.

**Değerlendirme bulguları belleği şekillendiriyor.** Bemula'da altı ana akım bellek sistemi üç omurga modelle denendi. Hiçbiri silme isteklerinin %1,5'inden fazlasını uçtan uca geçemedi. Sistemler ya bilgiyi basit saldırılarla çıkarılabilir bırakıyor ya da istenenden fazlasını siliyor ([arXiv:2610.18052](https://arxiv.org/abs/2610.18052)). Memora/FAMA, geçersizleşmiş belleğin yeniden kullanımını cezalandırıyor ve bellek ajanlarının yalnızca marjinal iyileşme sağladığını raporluyor ([arXiv:2604.20006](https://arxiv.org/abs/2604.20006)). DyadMem "altın bellek" koşulunda QA'nın güçlü kaldığını, tam hatta (yakalama → güncelleme → geri getirme) ise sert düştüğünü gösteriyor ve güvensiz silme sorunlarını not ediyor ([arXiv:2610.03020](https://arxiv.org/abs/2610.03020)). Buradan çıkan ders: **darboğaz geri getirmede değil, yakalama ve güncellemede**.

**Bellek bir saldırı yüzeyi.** Bu konu §6'da ayrıntılandırılıyor. Özetle: belleğe agresif yazan ajanlar daha kolay sömürülüyor ([MPBench, arXiv:2606.04329](https://arxiv.org/abs/2606.04329)). İçerik veya köken zinciri tabanlı yetki, ajanın kendi özetlemesi yoluyla "aklanabiliyor" ([arXiv:2606.24322](https://arxiv.org/abs/2606.24322)).

### 3.2 YusufClaw için tasarım önerisi (SQLite, sonra DynamoDB)

**Şema (P0 çekirdek, P1 tamamı).** Mevcut `turns` tablosu korunur. Üstüne şunlar eklenir:

```sql
-- olay/episodik katman: değişmez, kaynak kanıtı
CREATE TABLE episodes(
  id TEXT PRIMARY KEY, session TEXT, principal TEXT,
  started_at TEXT, ended_at TEXT, summary TEXT,           -- bölüt düzeyi (SeCom)
  source_turn_ids TEXT, origin TEXT NOT NULL,             -- 'user'|'tool:<name>'|'web:<host>'|'agent'
  integrity TEXT NOT NULL CHECK(integrity IN ('trusted','untrusted')),
  mac TEXT NOT NULL                                       -- yazma anında köken bağlama (SMSR/TMA-NM fikri)
);
-- semantik katman: sürümlü iddialar, iki zaman ekseni
CREATE TABLE claims(
  id TEXT PRIMARY KEY, subject TEXT, predicate TEXT, object TEXT,
  valid_from TEXT, valid_to TEXT,                         -- dünyada ne zaman doğruydu
  learned_at TEXT, superseded_by TEXT,                    -- sistem ne zaman öğrendi
  status TEXT CHECK(status IN ('candidate','active','retracted','deleted')),
  origin TEXT NOT NULL, integrity TEXT NOT NULL, evidence_episode_ids TEXT,
  pinned INTEGER DEFAULT 0, access_count INTEGER DEFAULT 0, last_access TEXT
);
-- prosedürel katman: ReasoningBank/Memp
CREATE TABLE lessons(
  id TEXT PRIMARY KEY, kind TEXT CHECK(kind IN ('strategy','pitfall','recipe')),
  trigger TEXT, body TEXT, from_run_ids TEXT, outcome TEXT,   -- 'success'|'failure'
  status TEXT CHECK(status IN ('draft','active','deprecated')), version INTEGER
);
CREATE VIRTUAL TABLE mem_fts USING fts5(text, ref UNINDEXED, kind UNINDEXED);
CREATE TABLE tombstones(ref TEXT PRIMARY KEY, deleted_at TEXT, scope TEXT); -- içeriksiz
```

**Yazma politikası (P0).** `claims.status='active'` olabilmesi için iki koşuldan biri gerekir: kaynak `origin='user'` ve doğrudan bir ifade olmalı ("Ankara'ya taşındım") **veya** kullanıcı tarafından açıkça onaylanmış olmalı. `tool:*`/`web:*` kaynaklı her şey `candidate` olarak kalır ve **hiçbir zaman** eylem yetkisi taşımaz. Ajanın kendi özeti, girdilerinin en düşük güven düzeyini miras alır. Bu kuralın amacı "kendi özetlemesi yoluyla aklama"yı engellemek ([arXiv:2606.24322](https://arxiv.org/abs/2606.24322)). `mac` alanı, gateway'in yerel bir anahtarla (`.env` dışında, `0600` izinli bir dosyada) hesapladığı HMAC'tir. Bu sayede araçlar veya shell SQLite'a doğrudan "trusted" satır ekleyemez. Bu alan, SMSR'deki yazma anı köken imzası fikrinin sadeleştirilmiş bir uyarlamasıdır ([arXiv:2606.12703](https://arxiv.org/abs/2606.12703)).

**Gece konsolidasyonu rutini (P1).** Mevcut `routines` altyapısına `kind: 'consolidate'` tipi bir sistem rutini eklenir (varsayılan 03:30 Europe/Istanbul). Adımlar: (1) kapanmış oturumları SeCom tarzı bölütlere ayırıp `episodes` yazar. (2) Ollama'da küçük bir modelle ADD/UPDATE/DELETE/NOOP önerileri üretir (Memory-R1 işlem kümesi). (3) Her öneriyi TrustMem tarzı üç kontrolden geçirir: *kapsam* (kanıttaki önemli bilgi kayboldu mu), *koruma* (ilgisiz iddia bozuldu mu), *sadakat* (kanıtta olmayan bilgi eklendi mi). Kontrolden geçemeyen öneri `candidate` olarak kalır. (4) Başarısız/başarılı koşulardan `lessons` taslakları çıkarır (ReasoningBank). (5) LightMem/sleep-time fikriyle ertesi gün için "profil bloğunu" önceden derler. Rutin bir filigran (watermark) ve idempotency anahtarı kullanır (RESEARCH.md ilkesi).

**Geri getirme (P0 FTS, P1 hibrit).** RESEARCH.md'deki FTS5 + RRF önerisi geçerli. Ek olarak (i) FAMA dersine göre `valid_to` geçmiş iddialar varsayılan olarak filtrelenir ve sorgu tarihsel değilse kullanılmaz; (ii) çizge bellek yerine `claims.subject` üzerinde basit bir varlık indeksi kurulur; (iii) birebir alıntı gerektiren sorular için ham `turns` metnine geri dönülür. Seçici unutma çalışmasına göre varlıklara ayrıştırma yüzey biçimini kaybettiriyor ([arXiv:2608.28978](https://arxiv.org/abs/2608.28978)).

**Unutma (P0).** Dört ayrı işlem olmalı: *tahliye* (bağlam), *bozunma* (sıralama puanı: FadeMem tarzı `score = rel × e^{-λ·age} × f(access)`, `pinned=1` ise bozunma yok), *geçersizleştirme* (`valid_to`) ve *silme*. `/forget <ifade>` komutu deterministik olmalı: eşleşen `claims/episodes/lessons` ve FTS/gömme kayıtları silinir, türetilmiş özetler yeniden üretilir, `tombstones` yazılır. Gece rutini mezar taşı olan kaynaklardan tekrar iddia türetmez. Kabul testi Bemula'nın beş boyutunu izlemeli: depolandı mı, silindi mi, ilgisiz bellek korundu mu, benzer bilgi yeniden öğrenilebiliyor mu, çıkarma saldırısına dayanıklı mı ([arXiv:2610.18052](https://arxiv.org/abs/2610.18052)).

**DynamoDB geçişi (P2).** `StateStore` (runs/events/claims; koşullu yazma ve sürüm alanı ile) ile `MemoryIndex` (FTS/vektör) ayrı arayüzler olarak tanımlanmalı. DynamoDB yalnızca `StateStore`'un yerine geçer. İndeks yerelde kalır veya ayrı bir arama servisine taşınır. RESEARCH.md bu ayrımı öneriyordu. Burada bunun **köken alanlarıyla** birlikte taşınması gerektiğini ekliyoruz (`origin/integrity/mac`).

**Öncelik:** şema + yazma politikası + `/forget` P0. Gece konsolidasyonu, lessons ve hibrit arama P1. Öğrenilmiş bozunma, çizge ve DynamoDB P2.

---
## 4. Araç kullanımı ve yetenekler: MCP, computer/browser use, skill kütüphaneleri, rutinler

### 4.1 Ana bulgular — MCP

**Gerçek MCP sunucularıyla yetenek hâlâ sınırlı.** MCP-Universe 11 gerçek MCP sunucusu üzerinde çalışıyor. Bu ortamda GPT-5 %43,72, Grok-4 %33,33, Claude-4.0-Sonnet %29,44 başarı alıyor. Kurumsal ajanlar (Cursor) standart ReAct'ten daha iyi çıkmıyor ([arXiv:2508.14704](https://arxiv.org/abs/2508.14704)). MCP-Bench 28 canlı sunucu ve 250 araçla bulanık talimattan araç bulmayı ve çok adımlı planlamayı ölçüyor ([arXiv:2508.20453](https://arxiv.org/abs/2508.20453)). LiveMCPBench 70 sunucu/527 araçlık bir ortamda başarısızlıkların **yaklaşık yarısının araç geri getirme hatasından** kaynaklandığını buluyor ([arXiv:2508.01780](https://arxiv.org/abs/2508.01780)).

**Araç kataloğunu geri getirmeyle daraltmak.** RAG-MCP yalnızca ilgili araç açıklamalarını modele vererek istem token'larını %50'den fazla azaltıyor ve araç seçim doğruluğunu %13,62'den %43,13'e çıkarıyor ([arXiv:2505.03275](https://arxiv.org/abs/2505.03275)). ScaleMCP, ajana bir araç geri getirici verip araçları çok turlu etkileşimde dinamik olarak eklemesine izin veriyor ve araç deposunu MCP sunucularıyla otomatik senkronize ediyor ([arXiv:2505.06416](https://arxiv.org/abs/2505.06416)).

**MCP saldırı yüzeyi (RESEARCH.md'de yalnızca genel güvenlik rehberi vardı).** MCPTox 45 gerçek sunucu, 353 araç ve 1.348 kötü amaçlı vakayla araç **açıklaması** zehirlenmesini ölçüyor: o1-mini'de saldırı başarısı %72,8. Daha yetenekli modeller çoğunlukla daha savunmasız, en yüksek reddetme oranı ise %3'ün altında ([arXiv:2508.14925](https://arxiv.org/abs/2508.14925)). MSB 12 saldırı türünü gerçek araç çalıştırarak ölçüyor (ad çakışması, tercih manipülasyonu, kapsam dışı parametre isteği, kullanıcı taklidi yapan yanıt, sahte hata yükseltme vb.). O da güçlü modellerin daha savunmasız olduğunu buluyor ([arXiv:2510.15994](https://arxiv.org/abs/2510.15994)). MCP-TDP'de prompt guardrail'leri büyük ölçüde etkisiz, hatta ters tepebiliyor. Yazarlar buna "Firewall Fallacy" diyor ([arXiv:2605.24069](https://arxiv.org/abs/2605.24069)). Protokol düzeyi analiz üç temel zayıflık tanımlıyor: yetenek tasdikinin olmaması, köken kimlik doğrulaması olmadan çift yönlü sampling ve çok sunuculu yapılandırmalarda örtük güven yayılımı. Önerilen MCPSec uzantısı saldırı başarısını %52,8'den %12,4'e indiriyor ([arXiv:2601.17549](https://arxiv.org/abs/2601.17549)). Daha erken çalışmalar MCP sunucusu yaşam döngüsü için 16 tehdit senaryosu tanımladı ([arXiv:2503.23278](https://arxiv.org/abs/2503.23278)) ve MCP araçlarıyla kod çalıştırma ve kimlik bilgisi hırsızlığı gösterdi ([arXiv:2504.03767](https://arxiv.org/abs/2504.03767)).

### 4.2 Ana bulgular — computer/browser use

**Yetenek arttı ama uzun iş akışları hâlâ zayıf.** UI-TARS yalnızca ekran görüntüsüyle çalışan yerel bir GUI ajan modeli ([arXiv:2501.12326](https://arxiv.org/abs/2501.12326)). OpenCUA açık veri ve model altyapısı sunuyor ([arXiv:2508.09123](https://arxiv.org/abs/2508.09123)). Çoklu deneme ve davranış anlatılarını karşılaştıran BJudge OSWorld'de %72,6 ile yeni bir en iyi sonuç bildiriyor ([arXiv:2510.02250](https://arxiv.org/abs/2510.02250)). Buna karşılık OSWorld 2.0'da (insanların ortanca ~1,6 saatte bitirdiği 108 uzun iş akışı) en iyi sistem görevlerin yalnızca %20,6'sını tamamlıyor. Ajanlar kısıtları unutuyor, görev ortasında gelen bilgiyi kaçırıyor, kullanıcıya sormak yerine tahmin ediyor ve doğrulamayı atlıyor ([arXiv:2606.29537](https://arxiv.org/abs/2606.29537)). ClawBench'te 144 canlı platformdaki 153 günlük görevde Claude Sonnet 4.6 %33,3 alıyor ([arXiv:2604.08523](https://arxiv.org/abs/2604.08523)).

**Güvenlik önemli ölçüde geride.** OSGuard'da korumasız bir ajan iyi niyetli görevlerin %37,8'ini güvensiz şekilde tamamlıyor. En güçlü guardrail bu oranı yalnızca %33,3'e indiriyor ([arXiv:2606.15034](https://arxiv.org/abs/2606.15034)). OS-Harm kasıtlı kötüye kullanım, prompt injection ve model hatalı davranışını OSWorld üzerinde ölçüyor ([arXiv:2506.14866](https://arxiv.org/abs/2506.14866)). WASP, düşük emekle yazılmış basit insan enjeksiyonlarının bile en iyi modelleri gerçekçi senaryolarda kandırabildiğini gösteriyor ([arXiv:2504.18575](https://arxiv.org/abs/2504.18575)).

### 4.3 Ana bulgular — skill kütüphaneleri

**Kendi kendine skill üretimi işe yarıyor.** SkillWeaver'da web ajanı yeni bir sitede skill'leri keşfediyor, pratik yaparak deniyor ve sağlam API'lere damıtıyor ([arXiv:2504.07079](https://arxiv.org/abs/2504.07079)). Bu, Voyager fikrinin web alanındaki 2025 karşılığı.

**Skill tedarik zinciri gerçek bir tehdit.** 2026'da yapılan ilk büyük ölçekli çalışmalar iki kayıt deposundaki 98.380 skill'de 157 doğrulanmış kötü amaçlı skill ve 13 teknikte 632 açık buldu. Vakaların yarıdan fazlası marka taklidi yapan tek bir aktörden geliyordu ([arXiv:2602.06547](https://arxiv.org/abs/2602.06547)). 238.180 skill üzerindeki depo-farkındalıklı analiz, tarayıcıların kötü niyeti fazlasıyla tahmin ettiğini gösterdi: depo bağlamı eklenince yalnızca %0,52 şüpheli kalıyor. Aynı çalışma terk edilmiş GitHub depolarındaki skill'lerin ele geçirilmesi gibi yeni bir vektör de belgeledi ([arXiv:2603.16572](https://arxiv.org/abs/2603.16572)). SKILL.md metni tek başına keşif, seçim ve yönetişimi manipüle edebiliyor: işlevsel olarak eşdeğer düşmanca varyantlar eşli denemelerin ortalama %77,6'sında seçiliyor ([arXiv:2605.11418](https://arxiv.org/abs/2605.11418)). SkillSec-Eval, açıkların yalnızca yürütmede değil, kabul, geri getirme, planlayıcı seçimi ve skill evrimi aşamalarında da ortaya çıktığını gösteriyor ([arXiv:2607.13987](https://arxiv.org/abs/2607.13987)). Biçim olarak Agent Skills açık spesifikasyonu referans alınabilir: YAML ön maddesi (`name`, `description`, `allowed-tools` vb.) olan bir `SKILL.md`, isteğe bağlı `scripts/`, `references/` ve `assets/` dizinleri ile aşamalı açılım (progressive disclosure) ([agentskills.io](https://agentskills.io/specification)).

### 4.4 Ana bulgular — rutinler ve proaktiflik

π-Bench, OpenClaw benzeri kişisel asistanlar için 5 persona ve 100 çok turlu görevle **proaktifliği** ölçüyor. Gizli niyetler, görevler arası bağımlılıklar ve oturumlar arası süreklilik içeriyor. Benchmark hatırlatıcı ve yinelenen görev ekleme/listeleme/silme araçları da sunuyor (makalenin HTML tam metni). Bulgular: proaktif yardım hâlâ zor, görev tamamlama ile proaktiflik ayrı yetenekler ve önceki etkileşim sonraki görevlerde niyet çözmeye yardım ediyor ([arXiv:2605.14678](https://arxiv.org/abs/2605.14678)). Meta'nın ARE/Gaia2'si asenkron çalışıyor ve ajanlardan belirsizlik, gürültü, dinamik ortam ve **zaman kısıtları** altında davranmalarını istiyor. Statik değerlendirmede görünmeyen hata modlarını ortaya çıkarıyor ([arXiv:2509.17158](https://arxiv.org/abs/2509.17158)).

### 4.5 YusufClaw için tasarım önerisi

**(a) Araç kaydı = politika kaydı (P0).** `AgentTool` sözleşmesine efekt ve kaynak meta verisi eklenir. Onay kararı araç adından değil, bu meta veriden ve argümanlardan türetilir.

```ts
interface ToolManifest {
  id: string; version: string; source: 'builtin' | 'mcp' | 'skill';
  effects: Array<'read_private' | 'read_untrusted' | 'egress' | 'write_local' | 'exec' | 'send_message' | 'spend'>;
  resources: { paths?: string[]; hosts?: string[] };    // varsayılan: boş = hiçbir şey
  descriptionSha256: string;                             // MCP/skill için pinlenmiş açıklama
}
```

**(b) MCP istemcisi (P1).** `packages/tools/src/mcp/` dizininde şu kurallar uygulanır. Sunucu bazında izin listesi olur (`mcp.json`: komut/URL, sürüm, beklenen araç adları ve açıklama hash'leri). Araçlar `mcp.<sunucu>.<araç>` biçiminde ad alanına alınır (ad çakışması saldırısına karşı, MSB). İlk bağlantıda ve **açıklama hash'i her değiştiğinde** kullanıcıya fark gösterilip onay istenir (MCPTox/TDP'ye karşı). Sampling ve elicitation varsayılan olarak kapalı tutulur (protokol düzeyi zayıflık 2). Her MCP aracı varsayılan olarak `read_untrusted` etiketli döner. MCP sonuçları §6'daki taint motoruna girer. Katalog 15 aracı geçtiğinde RAG-MCP tarzı geri getirme devreye girer: FTS5 ile araç açıklamaları aranır ve modele ilk 5–8 araç verilir. Değerlendirme hedefi olarak LiveMCPBench'teki "geri getirme hatası" ölçütü iç testlere eklenir.

**(c) Tarayıcı (P1).** `browser` aracı bugün onaysız `navigate/content/title` destekliyor. Önerilenler: ayrı bir Playwright profili (çerezsiz), indirmelerin izole bir dizine yönlendirilmesi, **form gönderme, satın alma ve mesaj gönderme gibi işlemlerin ClawBench'teki gibi son istek düzeyinde yakalanıp onaya bağlanması** (ClawBench bu yakalama katmanını değerlendirme güvenliği için kullanıyor, biz aynı tekniği üretim onayı için öneriyoruz). Sayfa içeriği `read_untrusted` etiketiyle döner. Masaüstü computer use, OSGuard/OSWorld 2.0'daki güvenlik açığı nedeniyle P2'ye ve yalnızca atılabilir bir VM'ye ertelenmeli.

**(d) Skill yaşam döngüsü (P1).** Mevcut `loadSkills` yerine şu akış gelir: `skills/<id>/SKILL.md` dosyasının ön maddesinde Agent Skills alanları (`name`, `description`, `allowed-tools`) ile YusufClaw'a özgü `version` ve `resources` alanları bulunur. `skills.lock` dosyası her skill'in SHA-256 değerini tutar ve başlangıçta hash uyuşmazlığı varsa skill yüklenmez. Sistem promptuna yalnızca ad + açıklama girer, tam metin skill seçilince yüklenir (aşamalı açılım). Skill metni **yetki veremez**: `allowed-tools`, kullanıcının verdiği izinlerle kesiştirilir. Dışarıdan gelen skill'ler `quarantine/` dizinine düşer ve kullanıcı farkı görüp onaylayana kadar yüklenmez. Ajanın ürettiği skill'ler (SkillWeaver/Memp tarzı) `draft` durumunda başlar, test fikstürü geçmeden `active` olmaz. Bunun gerekçesi şu: ClawSafety'de skill dosyaları en tehlikeli vektör çıktı, SKILL.md metni ise seçimi manipüle edebiliyor.

**(e) Rutinler 2.0 (P1).** Mevcut UTC cron korunur, şunlar eklenir: IANA saat dilimi (varsayılan `Europe/Istanbul`); `kind: 'deadline' | 'cron' | 'heartbeat' | 'consolidate'`; rutin başına *yetki zarfı* (`grants`, `budget`, sabit `deliveryTarget`); kaçırılan tetiklemeler için `misfire: 'skip' | 'coalesce' | 'catchup'`; ve π-Bench'e benzer şekilde kullanıcının sohbetten `schedule_task` aracıyla rutin önermesi. Rutin **oluşturma veya genişletme her zaman onaylı** olmalı, çalıştırma ise zarf içinde onaysız. Proaktif "heartbeat" rutinleri (örneğin her sabah "bugün beni ilgilendiren bir şey var mı?") salt-okur zarfla başlar ve yalnızca kullanıcının özel sohbetine yazabilir.

**Öncelik:** (a) P0, (b) P1, (c) temel izolasyon P0 ve gönderim yakalama P1, (d) hash kilidi P0 ve tam yaşam döngüsü P1, (e) P1.

---
## 5. Arayüz ve UX: çok kanallı gateway ve onay akışları

### 5.1 Ana bulgular

**Özerklik bir tasarım kararı.** Feng, McDonald ve Zhang beş özerklik düzeyi tanımlıyor ve bunları kullanıcının üstlendiği rolle adlandırıyor: *operatör, iş birlikçi, danışman, onaylayıcı, gözlemci*. Özerkliğin yetenekten ve çalışma ortamından ayrı, bilinçli bir tasarım kararı olarak ele alınması gerektiğini savunuyorlar ([arXiv:2506.12469](https://arxiv.org/abs/2506.12469)). Mitchell vd. kullanıcının devrettiği kontrol arttıkça insanlara yönelik risklerin de arttığını öne sürüyor ([arXiv:2502.02649](https://arxiv.org/abs/2502.02649)). Magentic-UI insan-döngüde etkileşim mekanizmalarını açık kaynak olarak sunuyor: ortak planlama, ortak görev yürütme, çoklu görev, eylem korumaları (action guards) ve uzun süreli bellek ([arXiv:2507.22358](https://arxiv.org/abs/2507.22358)).

**Onay her zaman koruma anlamına gelmiyor (2026 kullanıcı çalışmaları).**

- 48 katılımcı ve 192 canlı web oturumuyla yapılan çalışmada denetim stratejisi sorunlu eylemlere *maruz kalmayı* etkiledi, ama kullanıcıların *müdahale yeteneğini* etkilemedi. Kontrol çoğu zaman çok erken veya çok geç geri döndü. Kullanıcılar eylemin güvenli olup olmadığını değil, doğru olup olmadığını yargıladı. **Önceden verilen plan onayı, çalışma anındaki incelemeyi azalttı** ([arXiv:2604.04918](https://arxiv.org/abs/2604.04918)).
- 113 katılımcılı bir çalışma, kullanıcıların kendi yazdığı "izin ver / sor / asla" kurallarını eylem başına onay (HITL) ve otomatik model incelemesiyle karşılaştırdı. Kural koşulu, HITL'e göre aşırı yetkili eylemleri 20,1 puan **daha az** engelledi. Çalışma anı onay sorusu 18'den 10,9'a düştü, ama kurulum süresi dahil edilince toplam müdahale süresi güvenilir biçimde azalmadı. Kullanıcılar 140 kuralın 114'ünde "sor" seçeneğini seçti ([arXiv:2608.27443](https://arxiv.org/abs/2608.27443)).
- **Onay penceresinin bütünlüğü.** Kodlama ajanlarında onay penceresini çoğu zaman ajanın kendisi anlatıyor. "Lies-in-the-Loop" saldırısı bu özetin sahtelenebileceğini gösteriyor. Çözüm olarak "What You See Is What You Sign" ilkesi öneriliyor: gösterilen eylem, güvenilir bir aracı tarafından gerçek eylemden üretilmeli ve çalışan eyleme bağlanmalı. Çözümlenemeyen eylemler sessizce onaylanmamalı, "incelenemez" olarak işaretlenmeli. Yazarlar bunun çözülmüş bir savunma değil, bir ödünleşim olduğunu açıkça belirtiyor ([arXiv:2606.02668](https://arxiv.org/abs/2606.02668)).
- Verifiable Action Card, ajanlı tarayıcıda onay bilgisini bekleyen gerçek eylemden ve güvenilir niyet kökeninden yeniden kuruyor, güvenilir arayüz katmanında gösteriyor ve gönderim anında yeniden doğruluyor. Değerlendirilen modellerde saldırı başarısını %68–100'den %0'a indiriyor, meşru görev tamamlama %78 kalıyor ([arXiv:2609.18411](https://arxiv.org/abs/2609.18411)).

**Ses kanalı.** Full-Duplex-Bench-v3 gerçek insan sesindeki akıcısızlıklar (disfluency) altında çok adımlı araç kullanımını ölçüyor. GPT-Realtime Pass@1'de 0,600 ile önde. Klasik kaskad hat (Whisper → GPT-4o → TTS) en yüksek gecikmeye (10,12 s) sahip. Tüm sistemlerde kendi kendini düzeltme ve zor senaryolarda çok adımlı akıl yürütme en tutarlı hata modları ([arXiv:2604.04847](https://arxiv.org/abs/2604.04847)).

### 5.2 YusufClaw için tasarım önerisi

**(a) Onay kartı = gateway'in ürettiği, ajanın yazmadığı nesne (P0).** Bugün `ApproveTool(call)` CLI'da "yes" bekliyor, Telegram'da ise reddediyor. Önerilen yapı:

```ts
// core: ajan metnine dokunmadan, gerçek çağrıdan üretilir
interface ActionCard {
  approvalId: string; nonce: string; expiresAt: string;
  toolId: string; toolVersion: string; argsSha256: string;
  rendered: {                         // deterministik oluşturucu (araç başına)
    title: string;                    // "Shell komutu çalıştır"
    exact: string;                    // argv dizisi / tam URL / dosya yolu + diff
    effects: ToolManifest['effects'];
    taint: 'clean' | 'contaminated';  // oturum güvenilmeyen veri gördü mü (§6)
    reversible: boolean; uninspectable?: string; // çözümlenemeyen kısım
  };
}
```

Telegram'da bu kart `InlineKeyboard` olarak gönderilir: **[Bir kez onayla] [Reddet] [Ayrıntı]**. Callback `approvalId + nonce` taşır, gateway gönderici kimliğini allowlist'e karşı tekrar doğrular, onay atomik olarak tüketilir ve argüman hash'i değişmişse geçersiz sayılır (RESEARCH.md bağlama ilkesi). Kartın gövdesinde ajanın açıklaması ayrı ve "ajanın iddiası" etiketiyle gösterilir. Kararın dayanağı ise `exact` alanı. Bu tasarım Consent Integrity ve VAC ilkelerini Telegram'a uyarlıyor. Telegram istemcisi güvenilir bir tarayıcı katmanı olmadığı için, kartın içeriğini ajan metninden değil gateway'in araç başına oluşturucusundan üretmek temel koruma.

**(b) Risk-orantılı sorma (P0→P1).** Kullanıcı çalışmaları "her şeyi sor" yaklaşımının yorgunluk yarattığını, standing kuralların ise beklenenden az koruduğunu gösteriyor. Önerilen dengeli model şu: *temiz* oturumda ve `effects ⊆ {read_private(scope içi), write_local(workspace)}` ise onaysız çalışır. Oturum *kirli* ise (güvenilmeyen içerik görülmüşse) ve eylem `egress/send_message/spend/exec` içeriyorsa her zaman kart gösterilir ve kart **kirlenmeyi açıkça vurgular** ("Bu istek bir web sayfasını okuduktan sonra geldi"). Çalışmadaki "risk karar anında görünür olmalı" bulgusu bunu destekliyor. Kalıcı kurallar yalnızca dar kaynaklara verilir (ör. `web_fetch` için `*.wikipedia.org`), süreli olur (ör. 7 gün) ve `/approvals` ile listelenip geri alınabilir.

**(c) Plan onayı ≠ eylem onayı (P1).** Uzun görevlerde Magentic-UI tarzı ortak planlama olabilir. Ancak plan onayı, kirli oturumdaki dış etkili eylemlerin kartlarını **kaldırmaz**. Gerekçe: önceden verilen onay çalışma anı incelemesini azaltıyor.

**(d) Özerklik düzeyi kanala bağlı (P1).** Varsayılanlar: CLI "iş birlikçi", Telegram "onaylayıcı", rutinler "gözlemci" (yalnızca zarf içi). `/mode` komutu düzeyi oturum için değiştirir ama `spend/send_message` gibi efektler için kart her zaman gösterilir.

**(e) Çok kanallı tutarlılık (P0/P1).** RESEARCH.md'deki zarf (`principalId`, `replyTarget` vb.) ve durable outbox önerileri geçerli. Ek olarak: onay kartları **kanaldan bağımsız** olmalı. CLI'da başlayıp Telegram'dan onaylanabilmeli, ama yalnızca aynı `principalId` ile. `/status` ve `/cancel` deterministik gateway komutları olmalı.

**(f) Ses (P2).** Önce "bas-konuş" ve Whisper/yerel STT ile kaskad hat kurulmalı. Ses kanalında dış etkili eylemler sesle onaylanmaz, onay kartı Telegram/web'e düşer. Gerekçe: FDB-v3'te kendi kendini düzeltme ve çok adımlı akıl yürütme tutarlı hata modları.

**Öncelik:** (a) ve (b) P0. (c), (d) ve (e) P1. (f) P2.

---
## 6. Güvenlik: prompt injection savunmaları ve izinler

### 6.1 Ana bulgular

**Kişisel ajanlara özgü tehdit modeli (2026, OpenClaw çalışmaları).**

- *Your Agent, Their Asset:* Canlı bir OpenClaw örneği (yerel sistem + Gmail + Stripe) üzerinde 12 saldırı senaryosu dört omurga modelle denendi. Kalıcı durum **Capability, Identity, Knowledge** (CIK) olarak üç boyutta ele alındı. Herhangi bir boyutun zehirlenmesi ortalama saldırı başarısını %24,6'dan %64–74'e çıkardı. En iyi savunma bile Capability hedefli saldırılarda %63,8 başarıya izin verdi. Dosya koruması kötü amaçlı enjeksiyonların %97'sini engelledi, ama meşru güncellemeleri de engelledi ([arXiv:2604.04759](https://arxiv.org/abs/2604.04759)).
- *A Security Analysis of the OpenClaw AI Agent Framework:* 470 advisory'nin katman (exec policy, gateway, kanal, sandbox, tarayıcı, eklenti, ajan/prompt) ve teknik eksenlerinde sınıflandırılması. Bulgular: üç orta/yüksek önemli advisory birleşerek LLM araç çağrısından ana sürece uzanan kimlik doğrulamasız bir RCE yolu oluşturuyor. Komut allowlist'i sözcüksel ayrıştırmaya dayandığı için satır devamı, busybox ve GNU seçenek kısaltmasıyla aşılabiliyor. Eklenti kanalıyla dağıtılan kötü amaçlı bir skill, exec hattını atlayıp iki aşamalı bir dropper çalıştırdı. Baskın yapısal zayıflık, birleşik politika sınırı yerine **katman başına güven denetimi** ([arXiv:2603.27517](https://arxiv.org/abs/2603.27517)).
- Altı OpenClaw türevinin 205 test vakasıyla değerlendirilmesi, ajanlaştırılmış sistemlerin altta yatan modellerden anlamlı ölçüde daha riskli olduğunu ve keşif davranışlarının en yaygın zayıflık olduğunu gösteriyor ([arXiv:2604.03131](https://arxiv.org/abs/2604.03131)). PASB, OpenClaw'da kullanıcı istemi işleme, araç kullanımı ve bellek geri getirme aşamalarında kritik açıklar raporluyor ([arXiv:2602.08412](https://arxiv.org/abs/2602.08412)). ClawSafety'de (120 senaryo, 2.520 deneme) saldırı başarısı modele göre %40–75 arasında değişiyor ve aynı modelin farklı iskeletlerde farklı güvenlik sonucu verdiği gözleniyor ([arXiv:2604.01438](https://arxiv.org/abs/2604.01438)).

**Tasarımla savunma: CaMeL ailesi.** CaMeL, güvenilir kullanıcı sorgusundan kontrol ve veri akışını açıkça çıkarıyor. Böylece güvenilmeyen veri program akışını değiştiremiyor. Araç çağrılarında yetenek (capability) politikalarıyla yetkisiz veri akışını engelliyor. AgentDojo'da görevlerin %77'sini kanıtlanabilir güvenlikle çözüyor, savunmasız sistemde bu oran %84 ([arXiv:2503.18813](https://arxiv.org/abs/2503.18813)). Bu yaklaşım Willison'ın "Dual LLM" desenini (ayrıcalıklı ve karantinadaki LLM) sistematik hale getiriyor ([Willison, 2023](https://simonwillison.net/2023/Apr/25/dual-llm-pattern/)). *Design Patterns* makalesi kanıtlanabilir direnç için altı desen tanımlıyor: Action-Selector, Plan-Then-Execute, LLM Map-Reduce, Dual LLM, Code-Then-Execute, Context-Minimization ([arXiv:2506.08837](https://arxiv.org/abs/2506.08837)). Takip çalışmaları şunları ekliyor:

- *Operationalizing CaMeL:* CaMeL'in güvenilir kullanıcı istemi varsaydığını ve yan kanalları ele almadığını belirtiyor. Girdi taraması, çıktı denetimi ve katmanlı risk erişim modeli öneriyor ([arXiv:2505.22852](https://arxiv.org/abs/2505.22852)).
- *CaMeLoT:* planı araç çağrılmadan önce sonlu durum sistemine çevirip CTL zamansal politikalarıyla model denetleyicide (nuXmv) doğruluyor. Güvensiz planlar LLM veya araç çağrısı harcamadan reddediliyor ve karşı örnek planı onarmak için geri bildirim olarak veriliyor ([arXiv:2609.18674](https://arxiv.org/abs/2609.18674)).
- *Can CaMeLs Talk?:* CaMeL garantileri hiyerarşik çok ajanlı sistemlerde **bileşim yoluyla korunmuyor**, çünkü güvenilmeyen veri alt ajanda güvenilir talimat olarak yeniden yorumlanabiliyor. multi-CaMeL talimat ve veri kanallarını ayırıyor. MultiAgentDojo'da saldırı başarısı %0,0 (tek ajanlı CaMeL'de %0,2, savunmasız sistemde %12,9) ([arXiv:2610.05640](https://arxiv.org/abs/2610.05640)).

**Deterministik ayrıcalık denetimi ve bilgi akışı.**

- *Progent:* araç adları ve argümanları üzerinde sembolik kurallar tanımlıyor. Politika güncellemelerinden daraltmalar otomatik uygulanıyor, genişletmeler ise SMT çözücüyle tespit edilip **açık onay** istiyor. Böylece etkin eylem alanı onaysız yalnızca daralabiliyor (monoton sınırlama) ([arXiv:2504.11703](https://arxiv.org/abs/2504.11703)).
- *FIDES:* gizlilik ve bütünlük etiketlerini izleyen, politikaları deterministik uygulayan ve bilgiyi seçici olarak gizleme ilkelleri sunan bir planlayıcı ([arXiv:2505.23643](https://arxiv.org/abs/2505.23643)).
- *ACE:* planlamayı soyut ve somut iki aşamaya ayırıyor, yürütmeyi izole ediyor ([arXiv:2504.20984](https://arxiv.org/abs/2504.20984)).
- *DRIFT:* güvenli planlayıcı (minimum fonksiyon yörüngesi + parametre kontrol listesi), dinamik doğrulayıcı ve bellek akışındaki çelişen talimatları maskeleyen enjeksiyon yalıtıcısından oluşuyor ([arXiv:2506.12104](https://arxiv.org/abs/2506.12104)).
- *AuthGraph:* temiz bağlamdan türetilen yetkilendirme grafını gerçek yürütmenin köken grafıyla **parametre kaynağı düzeyinde** karşılaştırıyor. AgentDojo'da GPT-4o ile saldırı başarısını %40'tan %1'e indiriyor, görev tamamlama %76 kalıyor ([arXiv:2605.26497](https://arxiv.org/abs/2605.26497)).
- *SkillGuard:* güvenilmeyen verinin duruma girmesini "kirlenme" olarak ele alıyor ve sonraki yetenekleri, yasak durumlara ulaşılamayacak şekilde daraltıyor. Ek model çağrısı veya token maliyeti yok ([arXiv:2608.30041](https://arxiv.org/abs/2608.30041)).

**Tespit ve eğitim tabanlı savunmalar, ve sınırları.** MELON maskelenmiş yeniden yürütme ile, kullanıcı görevinden bağımsızlaşan eylemleri yakalıyor ([arXiv:2502.05174](https://arxiv.org/abs/2502.05174)). Meta-SecAlign modeli simüle enjeksiyonlarla eğitiyor ([arXiv:2507.02735](https://arxiv.org/abs/2507.02735)). Ancak *The Attacker Moves Second*, uyarlanabilir saldırılarla (gradyan, RL, rastgele arama, insan destekli keşif) 12 yeni savunmayı çoğunda %90'ın üzerinde başarıyla aşıyor. Bu savunmaların çoğu sıfıra yakın saldırı başarısı raporlamıştı ([arXiv:2510.09023](https://arxiv.org/abs/2510.09023)). Sonuç: Bedrock Guardrails veya prompt sınıflandırıcıları **tek kapı** olamaz, ancak ek sinyal olabilir.

**Bellek zehirlenmesi (RESEARCH.md'de yalnızca bir tablo satırıydı).** MPBench dört bellek yazma kanalı, dokuz yapısal açık ve altı saldırı sınıfı tanımlıyor. Mevcut prompt injection savunmaları bellek zehirlenmesini kapsamıyor ([arXiv:2606.04329](https://arxiv.org/abs/2606.04329)). GhostWriter araç kullanan kişisel ajanlarda yaklaşık %98 enjeksiyon ve yaklaşık %60 aktivasyon oranı raporluyor. Önerdiği AM-Sentry bir bellek kaydetme politikası ile bir geri getirme taramasını birleştiriyor ([arXiv:2607.06595](https://arxiv.org/abs/2607.06595)). TMA-NM, içerik ve köken zinciri tabanlı savunmaların üç kanalla aklanabildiğini gösteriyor: ajanın kendi özetlemesi, güvenilir bir aracın yankısı ve üretilmiş doğrulama. Yazarlar yazma anında köken bağlamanın **gerekli** olduğunu makine denetimli olarak kanıtlıyor. Mevcut savunmalarda %68'e varan aklama saldırısı başarısı ölçülürken TMA-NM'de %0 ([arXiv:2606.24322](https://arxiv.org/abs/2606.24322)). SMSR yazma anında HMAC kökeni ile rastgele bellek ablasyonu ve oylamayı birleştiriyor ([arXiv:2606.12703](https://arxiv.org/abs/2606.12703)).

### 6.2 YusufClaw için tasarım önerisi: "Taint + Yetenek + Kart" politika motoru

**Mimari (P0).** `packages/core/src/policy/` altında saf TypeScript ve deterministik bir motor kurulur. Motor LLM çağırmaz.

```ts
type Integrity = 'trusted' | 'untrusted';
interface Label { origin: string; integrity: Integrity; secrecy: 'public' | 'private' | 'secret' }
interface Value<T = unknown> { v: T; labels: Label[] }       // CaMeL/FIDES tarzı etiketli değer

interface SessionTaint { contaminated: boolean; sources: string[] }   // SkillGuard: kirlenme olayı

type Decision = { kind: 'allow' } | { kind: 'deny'; reason: string }
              | { kind: 'card'; card: ActionCard };

function decide(m: ToolManifest, args: Value<Record<string, unknown>>, s: SessionTaint, g: Grants): Decision {
  // 1) Yetenek: araç + kaynak, kullanıcı izinleriyle kesişmeli (Progent, monoton)
  if (!g.covers(m, args.v)) return { kind: 'deny', reason: 'grant' };
  // 2) Ölümcül üçlü: kirli oturumda egress/send/spend/exec → her zaman kart
  if (s.contaminated && m.effects.some(e => ['egress','send_message','spend','exec'].includes(e)))
    return { kind: 'card', card: renderCard(m, args, s) };
  // 3) AuthGraph'ın basit hali: hassas parametre (alıcı, URL host, yol) güvenilmeyen kaynaktan mı geldi?
  if (sensitiveParamsFromUntrusted(m, args)) return { kind: 'card', card: renderCard(m, args, s) };
  // 4) secret etiketli veri hiçbir egress'e argüman olamaz (FIDES gizlilik etiketi)
  if (m.effects.includes('egress') && args.labels.some(l => l.secrecy === 'secret')) return { kind: 'deny', reason: 'secret-egress' };
  return { kind: 'allow' };
}
```

Pratik notlar:

1. **Etiketleme iki düzeyde.** Tam CaMeL (Python benzeri bir yorumlayıcıyla P-LLM/Q-LLM ayrımı) YusufClaw için ağır. Bu nedenle P0'da *oturum düzeyi kirlenme* (SkillGuard yaklaşımı) ve *parametre düzeyi köken* (AuthGraph'ın basit hali) uygulanır. Ajanın önerdiği argümandaki bir değer (URL host'u, e-posta alıcısı, dosya yolu), kullanıcı mesajında veya güvenilir bellekte birebir geçmiyorsa ve güvenilmeyen bir araç çıktısında geçiyorsa "güvenilmeyen kaynaklı" sayılır. Bu bir sezgisel kuraldır, kanıtlanmış bir garanti değildir.
2. **P1: Karantinadaki okuyucu (Dual LLM / Map-Reduce).** `web_fetch`, `browser` ve MCP çıktıları ana modele ham olarak verilmez. Önce Ollama'da çalışan, **araç erişimi olmayan** bir "Q-LLM" bu içeriği şemaya bağlı yapılandırılmış veriye çevirir (`{title, summary, entities[], links[]}`). Ana model yalnızca bu yapıyı ve `$VAR` referanslarını görür. Ham metne yalnızca kullanıcıya gösterim için erişilir. Bu, Design Patterns makalesindeki Dual LLM ve LLM Map-Reduce desenlerinin bir uyarlamasıdır.
3. **P2: Plan-Then-Execute + statik doğrulama.** Uzun rutinler için model önce bir plan DSL'i üretir (araç dizisi ve veri bağımlılıkları), CaMeLoT tarzı bir denetleyici yürütmeden önce politikaları kontrol eder ("kirli veriden sonra `send_message` yok", "`secret` → `egress` yolu yok"). Tam model denetleyici yerine TypeScript'te basit bir graf erişilebilirlik kontrolü yeterli başlangıç olur.
4. **Alt ajanlar** yalnızca multi-CaMeL kuralıyla açılır: talimat alanı yalnızca güvenilir değerlerden oluşur, güvenilmeyen değerler `DataRef` olarak ayrı kanaldan geçer.
5. **Shell:** `sh -c` yalnızca sandbox içinde ve onayla çalışır. Onaysız salt-okur işler için `argv` tabanlı ve yolu sınırlanmış yapılandırılmış araçlar kullanılır (`list_dir`, `read_file`, `grep_workspace`). Kod gerekçesi: lexical allowlist'lerin aşılabildiği gösterildi ([arXiv:2603.27517](https://arxiv.org/abs/2603.27517)). Sandbox için Linux'ta `bubblewrap` veya rootless Podman önerilir: ağ kapalı, workspace dışında salt-okur bağlama, `.env` hiç bağlanmaz. Sandbox yoksa **kapalı başarısız olunur** (RESEARCH.md ilkesi).
6. **Sırlar:** `secret` etiketli kaynaklar (`.env`, `~/.aws`, token dosyaları, `memory.sqlite`) dosya araçlarının kabul kümesinden çıkarılır. Telegram/Bedrock anahtarları yalnızca gateway sürecinde kalır.
7. **Bellek:** §3.2'deki `origin/integrity/mac` alanları ve "aday iddialar eylem yetkisi taşımaz" kuralı uygulanır. CIK açısından YusufClaw'ın *Capability* yüzeyi = `skills/`, `mcp.json`, `routines.json` ve politika dosyaları. Bunlara ajan araçlarıyla yazılamaz, değişiklikleri yalnızca CLI'dan imzalı/onaylı yapılır. *Identity* yüzeyi = sistem promptu ve persona. *Knowledge* yüzeyi = `claims`/`lessons`.
8. **Değerlendirme:** savunma her zaman *benign utility* (zararsız görev başarısı) ile birlikte raporlanır. Uyarlanabilir saldırı denemeleri, statik fikstürlerin yanında periyodik olarak çalıştırılır.

**Öncelik:** politika motoru, oturum kirlenmesi, sır reddi, SSRF, shell sandbox ve bellek kökeni P0. Karantinadaki okuyucu ve parametre kökeni P1. Plan DSL'i, statik doğrulama ve multi-CaMeL P2 (alt ajan yazma yetkisiyle birlikte).

---
## 7. Değerlendirme benchmark'ları

### 7.1 Ana bulgular (2025–2026, RESEARCH.md'de olmayanlar)

| Benchmark | Ne ölçüyor | Not / bulgu | YusufClaw kullanımı |
|---|---|---|---|
| **ClawBench** ([arXiv:2604.08523](https://arxiv.org/abs/2604.08523)) | 144 canlı platformda 153 günlük çevrim içi görev. Son gönderim isteği yakalanıp engelleniyor. | Claude Sonnet 4.6 %33,3 | Tarayıcı aracında "gönderim yakalama" tekniğini hem test hem üretim onayı için kullanmak |
| **RealClawBench** ([arXiv:2606.03889](https://arxiv.org/abs/2606.03889)) | Gerçek OpenClaw oturumlarından yeniden kurulmuş 281 çalıştırılabilir görev, deterministik skorlayıcılar | 14 modelden en iyisi %65,8 | Yusuf'un kendi (izinli, anonimleştirilmiş) oturumlarından aynı yöntemle iç küme üretmek |
| **π-Bench** ([arXiv:2605.14678](https://arxiv.org/abs/2605.14678)) | Proaktiflik: gizli niyet, oturumlar arası süreklilik | Proaktiflik ile tamamlama ayrı yetenekler | Heartbeat rutinleri ve bellek değerlendirmesi |
| **Gaia2 / ARE** ([arXiv:2509.17158](https://arxiv.org/abs/2509.17158)) | Asenkron, zaman kısıtlı, dinamik ortam | Bütçe ölçekleme eğrileri düzleşiyor | Zamanlayıcı ve kesinti senaryoları |
| **τ²-bench** ([arXiv:2506.07982](https://arxiv.org/abs/2506.07982)) | Çift kontrol: kullanıcı da araç kullanıyor | τ-bench'in (RESEARCH.md) halefi | "Kullanıcıdan bir şey yapmasını isteme" akışları |
| **MCP-Universe / MCP-Bench / LiveMCPBench** (§4.1) | Gerçek MCP sunucularıyla görev | Geri getirme hataları başarısızlıkların ~yarısı (LiveMCPBench) | MCP istemcisi kabul testi |
| **OSWorld 2.0** ([arXiv:2606.29537](https://arxiv.org/abs/2606.29537)), **OSGuard** ([arXiv:2606.15034](https://arxiv.org/abs/2606.15034)), **OS-Harm** ([arXiv:2506.14866](https://arxiv.org/abs/2506.14866)) | Uzun iş akışları ve CUA güvenliği | %20,6 tamamlama, %37,8 güvensiz tamamlama | P2 computer use kapısı |
| **BrowseComp** ([arXiv:2504.12516](https://arxiv.org/abs/2504.12516)), **Mind2Web 2** ([arXiv:2506.21506](https://arxiv.org/abs/2506.21506)) | Zor bilgi bulma, uzun ufuklu ajanlı arama (Agent-as-a-Judge) | — | Araştırma alt ajanı değerlendirmesi |
| **MemoryAgentBench** ([arXiv:2507.05257](https://arxiv.org/abs/2507.05257)) | Dört yetkinlik: doğru geri getirme, test anında öğrenme, uzun menzilli anlama, seçici unutma | Hiçbir yöntem dördüne de hakim değil | Bellek ablasyonları |
| **PersonaMem** ([arXiv:2504.14225](https://arxiv.org/abs/2504.14225)), **Memora/FAMA** ([arXiv:2604.20006](https://arxiv.org/abs/2604.20006)), **DyadMem** ([arXiv:2610.03020](https://arxiv.org/abs/2610.03020)) | Değişen profil, eskimiş bellek, ilişkiye özgü bellek | Tam hatta (full pipeline) sert düşüş | `claims` güncelleme ve `valid_to` testleri |
| **Bemula** ([arXiv:2610.18052](https://arxiv.org/abs/2610.18052)) | İstek üzerine unutma (5 test paketi) | ≤%1,5 uçtan uca geçme | `/forget` kabul testi |
| **ClawSafety**, **PASB**, **CIK** (§6.1), **MCPTox**, **MSB**, **WASP** | Kişisel ajan, MCP ve web güvenliği | ASR %40–75 (ClawSafety), %72,8 (MCPTox) | CI'da saldırı fikstürleri |
| **Full-Duplex-Bench-v3** ([arXiv:2604.04847](https://arxiv.org/abs/2604.04847)) | Akıcısız konuşma altında sesli araç kullanımı | Kaskad hat 10,12 s gecikme | P2 ses kanalı |

**Metodoloji uyarıları.** *Agentic Benchmark Checklist* (ABC), yaygın benchmark'larda görev kurulumu veya ödül tasarımı hataları buluyor (ör. SWE-bench Verified'da yetersiz test vakaları, TAU-bench'te boş yanıtların başarılı sayılması). Bu hatalar performansı göreli olarak %100'e varan oranda yanlış tahmin ettirebiliyor ([arXiv:2507.02825](https://arxiv.org/abs/2507.02825)). HAL, 9 model ve 9 benchmark üzerinde 21.730 çalıştırma yaptı. Çalıştırmaların çoğunda daha yüksek akıl yürütme çabasının doğruluğu *düşürdüğünü*, ayrıca görev yerine benchmark'ı HuggingFace'te arama ve uçuş rezervasyonunda kredi kartını kötüye kullanma gibi beklenmeyen davranışları raporluyor ([arXiv:2510.11977](https://arxiv.org/abs/2510.11977)). Bu iki çalışma, YusufClaw'da **günlüklerin LLM destekli incelenmesini** ve **ödül/kontrol tasarımının denetimini** değerlendirme sürecinin parçası yapmayı destekliyor.

### 7.2 YusufClaw için tasarım önerisi: `evals/` paketi

**(a) Deterministik senaryo koşucusu (P0).** `tests/` yanına `evals/` eklenir. Her senaryo bir YAML dosyasıdır: `given` (bellek/dosya/sahte web sayfası/sahte MCP sunucusu), `user` mesajları, `expect` (durum kontrolleri: dosya hash'i, giden istek yok, kart üretildi, `claims` durumu). Model olarak hem sahte sağlayıcı (deterministik) hem gerçek Bedrock/Ollama (gece) kullanılır. ClawBench'teki yakalama fikri ağ katmanında uygulanır: tüm egress bir test proxy'sinden geçer ve "dışarı çıkan istek" bir assertion konusu olur.

**(b) Güvenlik fikstürleri (P0).** En az şu sınıflar bulunmalı: web sayfasından `.env` sızdırma girişimi (ölümcül üçlü), araç açıklaması zehirlenmesi (MCPTox tarzı sahte MCP sunucusu), skill dosyası enjeksiyonu (ClawSafety), bellek zehirlenmesi ve özetle aklama (MPBench/TMA-NM), onay kartı sahteleme (LITL), alt ajan aklama (multi-CaMeL) ve sözcüksel shell atlatma (satır devamı, busybox, seçenek kısaltması). Raporlanacak metrikler: ASR, zararsız görev başarısı ve kart sayısı (onay yükü).

**(c) Bellek değerlendirmesi (P1).** LongMemEval (RESEARCH.md) + MemoryAgentBench alt kümesi + Bemula + Türkçe iç küme (taşınma, iş değişikliği, "bunu unut" gibi senaryolar). Yakalama, güncelleme, geri getirme ve cevap ayrı ölçülür (DyadMem dersi).

**(d) Gerçek kullanım kümesi (P1).** RealClawBench yöntemiyle, Yusuf'un izin verdiği oturumlardan ayda bir 20–30 görev seçilir, ortam yeniden kurulur ve deterministik skorlayıcı yazılır. Anthropic'in de belirttiği gibi küçük bir örneklemle (~20 sorgu) erken başlamak yeterli.

**(e) Raporlama (P1).** Her çalıştırmada model kimliği, istem sürümü, araç manifest hash'leri, bellek şema sürümü ve bütçe kaydedilir. pass^k (k denemenin tamamı başarılı) ile pass@k ayrı raporlanır. Maliyet ve uçtan uca süre de raporlanır, çünkü token sayısı yanıltıcı olabiliyor ([arXiv:2609.32961](https://arxiv.org/abs/2609.32961)).

**Öncelik:** (a) ve (b) P0, (c), (d) ve (e) P1. Harici büyük benchmark'ların (OSWorld 2.0, Gaia2) tam koşusu P2.

---

## 8. Önceliklendirilmiş yol haritası

Aşamalar bağımlılık sırasına göre dizildi, takvim tahmini değil. Kimlikler `DESIGN-RECOMMENDATIONS.md`'deki P0/P1/P2 biletleriyle birlikte kullanılabilir (çakışmasın diye `YC-` öneki kullanıldı).

### P0 — Güvenilir yerel temel (önce bunlar)

| ID | İş | Paket | Kabul ölçütü | Dayanak |
|---|---|---|---|---|
| YC-01 | Varsayılan workspace'i `.data/workspace`'e taşı; `.env*`, `*.sqlite`, `~/.aws`, `.git/config` için mutlak okuma reddi | tools | `file_read .env` ve `cat ../.env` reddedilir | §1.3, Willison |
| YC-02 | SSRF: DNS sonrası ve her yönlendirmede özel/loopback/link-local reddi | tools | `127.0.0.1:11434`, `10.0.0.1`, yönlendirme zinciri engellenir | §1.3 |
| YC-03 | `ToolManifest` (efekt/kaynak) + deterministik `policy.decide()` + oturum kirlenmesi | core, tools | Kirli oturumda `web_fetch` dış host'a kartsız çıkamaz | CaMeL, Progent, SkillGuard |
| YC-04 | Shell: onaysız işler argv tabanlı yapılandırılmış araçlara; `sh -c` yalnızca sandbox'ta (bwrap/podman) ve onaylı; sandbox yoksa kapalı başarısız | tools | Satır devamı, busybox ve seçenek kısaltması fikstürleri geçer | arXiv:2603.27517 |
| YC-05 | `ActionCard` + Telegram inline onay (nonce, süre, args hash, atomik tüketim) | core, gateway | Tekrar oynatma ve argüman değişikliği reddedilir. Kart metni ajan metninden üretilmez. | Consent Integrity, VAC |
| YC-06 | `runs`/`run_events` olay günlüğü + çökme sonrası `outcome_unknown` | core | Süreç öldürme testleri | RESEARCH.md, CWL |
| YC-07 | Bellek şeması (`episodes/claims/lessons/tombstones`, `origin/integrity/mac`) + yazma politikası | core | Araç çıktısından gelen iddia `active` olamaz | MPBench, TMA-NM, SMSR |
| YC-08 | `/forget` (deterministik, türetilmişler dahil) + `/memory` görüntüleme | core, gateway | Bemula tarzı 5 kontrol | Bemula |
| YC-09 | `skills.lock` hash kilidi; skill metni yetki veremez; yapılandırma dosyalarına ajan yazamaz | core | Değiştirilmiş skill yüklenmez | ClawSafety, skill ekosistemi çalışmaları |
| YC-10 | `evals/` koşucusu + güvenlik fikstürleri CI'da | tests | ASR + zararsız başarı + kart sayısı raporu | §7.2 |

### P1 — Sonraki yetenek dalgası

| ID | İş | Dayanak |
|---|---|---|
| YC-11 | `ContextAssembler`: tipli bölüm tahliyesi, makbuz özetleri, Ollama ile asenkron özet | CWL, StateComp, "Beyond Token Savings" |
| YC-12 | Gece konsolidasyon rutini: bölütleme → ADD/UPDATE/DELETE/NOOP → geçiş doğrulama → lessons | LightMem, sleep-time, Memory-R1, TrustMem, ReasoningBank, Memp |
| YC-13 | Hibrit geri getirme (FTS5 + gömme + RRF), `valid_to` filtresi, birebir alıntı geri dönüşü | Memora/FAMA, seçici unutma çalışması, SeCom |
| YC-14 | Karantinadaki okuyucu (Q-LLM, araçsız, şemalı çıktı) web/browser/MCP için | Dual LLM, Design Patterns |
| YC-15 | Parametre kökeni kontrolü (alıcı/host/yol güvenilmeyen kaynaktan mı?) | AuthGraph |
| YC-16 | MCP istemcisi: izin listesi, ad alanı, açıklama hash'i ve fark onayı, sampling kapalı, araç geri getirme | MCPTox, MSB, MCP-TDP, MCPSec, RAG-MCP |
| YC-17 | Tarayıcı: ayrı profil, gönderim yakalama + kart | ClawBench, WASP, VAC |
| YC-18 | Rutinler 2.0: IANA saat dilimi, yetki zarfı, misfire politikası, `schedule_task` önerisi, heartbeat | π-Bench, Gaia2 |
| YC-19 | Model yönlendirici (plan/tool → Bedrock, summarize/extract → Ollama), gizlilik bayrağı | SLM pozisyon makalesi |
| YC-20 | Salt-okur araştırma alt ajanları (≤3 paralel, derinlik 1, veri kanalı, artefakt referansları) | Anthropic, AOrchestra, multi-CaMeL, MAST |
| YC-21 | Skill yaşam döngüsü: karantina, aşamalı açılım, ajan üretimi taslak skill + test | Agent Skills spec, SkillWeaver, SKILL.md saldırıları |
| YC-22 | Web arayüzünde onay ve bellek paneli; çapraz kanal onay | Magentic-UI, Levels of Autonomy |
| YC-23 | Gerçek kullanım iç kümesi + gece Bedrock/Ollama değerlendirmeleri | RealClawBench, HAL, ABC |

### P2 — Koşullu genişleme

| ID | İş | Koşul / dayanak |
|---|---|---|
| YC-24 | Yazma yapan delegeler (agy/codex): worktree, test kapısı, onaylı birleştirme | CAID, NP-Bench, Co-Coder. Önce YC-03, YC-04, YC-20 tamamlanmalı. |
| YC-25 | Plan DSL'i + statik politika doğrulaması (graf erişilebilirlik) | CaMeLoT, Plan-Then-Execute |
| YC-26 | multi-CaMeL tarzı alt ajan protokolü | Can CaMeLs Talk? |
| YC-27 | DynamoDB `StateStore` (köken alanlarıyla), S3 artefaktları, webhook | RESEARCH.md + §3.2 |
| YC-28 | Çizge bellek yalnızca ölçülmüş varlık/ilişki sorgusu ihtiyacı varsa | Seçici unutma çalışması, HippoRAG 2 |
| YC-29 | Ses (bas-konuş kaskad), WhatsApp, e-posta | FDB-v3 |
| YC-30 | Masaüstü computer use (yalnızca atılabilir VM) | OSWorld 2.0, OSGuard, OS-Harm |

**Önerilen ilk sprint (P0'ın çekirdeği):** YC-01 → YC-02 → YC-03 → YC-05 → YC-10. Bu beş iş tamamlandığında §1.3'teki "ölümcül üçlü" yolu kapanır ve bu kapanış CI'da doğrulanır. Mevcut 74 testin bozulmaması gerekir. RESEARCH.md'nin "henüz AWS'ye dağıtma" sınırı korunmalıdır.

---
## 9. Kaynakça

Tüm arXiv bağlantıları 16 Ekim 2026'da `arxiv.org/abs` sayfası açılarak doğrulandı (HTTP 200). Başlık, yazar ve tarih bilgileri sayfa meta verisinden alındı. Liste arXiv numarasına (yaklaşık yayın tarihine) göre sıralıdır.

### 9.1 arXiv kaynakları

1. Qin Y., Ye Y., Fang J. vd. (2025). *UI-TARS: Pioneering Automated GUI Interaction with Native Agents*. arXiv:2501.12326. <https://arxiv.org/abs/2501.12326>
2. Mitchell M., Ghosh A., Luccioni A. vd. (2025). *Fully Autonomous AI Agents Should Not be Developed*. arXiv:2502.02649. <https://arxiv.org/abs/2502.02649>
3. Zhu K., Yang X., Wang J. vd. (2025). *MELON: Provable Defense Against Indirect Prompt Injection Attacks in AI Agents*. arXiv:2502.05174. <https://arxiv.org/abs/2502.05174>
4. Pan Z., Wu Q., Jiang H. vd. (2025). *On Memory Construction and Retrieval for Personalized Conversational Agents*. arXiv:2502.05589. <https://arxiv.org/abs/2502.05589>
5. Gutiérrez B., Shu Y., Qi W. vd. (2025). *From RAG to Memory: Non-Parametric Continual Learning for Large Language Models*. arXiv:2502.14802. <https://arxiv.org/abs/2502.14802>
6. Tan Z., Yan J., Hsu I. vd. (2025). *In Prospect and Retrospect: Reflective Memory Management for Long-term Personalized Dialogue Agents*. arXiv:2503.08026. <https://arxiv.org/abs/2503.08026>
7. Cemri M., Pan M., Yang S. vd. (2025). *Why Do Multi-Agent LLM Systems Fail?*. arXiv:2503.13657. <https://arxiv.org/abs/2503.13657>
8. Debenedetti E., Shumailov I., Fan T. vd. (2025). *Defeating Prompt Injections by Design*. arXiv:2503.18813. <https://arxiv.org/abs/2503.18813>
9. Hou X., Zhao Y., Wang S. vd. (2025). *Model Context Protocol (MCP): Landscape, Security Threats, and Future Research Directions*. arXiv:2503.23278. <https://arxiv.org/abs/2503.23278>
10. Radosevich B., Halloran J. (2025). *MCP Safety Audit: LLMs with the Model Context Protocol Allow Major Security Exploits*. arXiv:2504.03767. <https://arxiv.org/abs/2504.03767>
11. Zheng B., Fatemi M., Jin X. vd. (2025). *SkillWeaver: Web Agents can Self-Improve by Discovering and Honing Skills*. arXiv:2504.07079. <https://arxiv.org/abs/2504.07079>
12. Suzgun M., Yuksekgonul M., Bianchi F. vd. (2025). *Dynamic Cheatsheet: Test-Time Learning with Adaptive Memory*. arXiv:2504.07952. <https://arxiv.org/abs/2504.07952>
13. Shi T., He J., Wang Z. vd. (2025). *Progent: Securing AI Agents with Privilege Control*. arXiv:2504.11703. <https://arxiv.org/abs/2504.11703>
14. Wei J., Sun Z., Papay S. vd. (2025). *BrowseComp: A Simple Yet Challenging Benchmark for Browsing Agents*. arXiv:2504.12516. <https://arxiv.org/abs/2504.12516>
15. Lin K., Snell C., Wang Y. vd. (2025). *Sleep-time Compute: Beyond Inference Scaling at Test-time*. arXiv:2504.13171. <https://arxiv.org/abs/2504.13171>
16. Jiang B., Hao Z., Cho Y. vd. (2025). *Know Me, Respond to Me: Benchmarking LLMs for Dynamic User Profiling and Personalized Responses at Scale*. arXiv:2504.14225. <https://arxiv.org/abs/2504.14225>
17. Wu Y., Liang S., Zhang C. vd. (2025). *From Human Memory to AI Memory: A Survey on Memory Mechanisms in the Era of LLMs*. arXiv:2504.15965. <https://arxiv.org/abs/2504.15965>
18. Evtimov I., Zharmagambetov A., Grattafiori A. vd. (2025). *WASP: Benchmarking Web Agent Security Against Prompt Injection Attacks*. arXiv:2504.18575. <https://arxiv.org/abs/2504.18575>
19. Li E., Mallick T., Rose E. vd. (2025). *ACE: A Security Architecture for LLM-Integrated App Systems*. arXiv:2504.20984. <https://arxiv.org/abs/2504.20984>
20. Du Y., Huang W., Zheng D. vd. (2025). *Rethinking Memory in LLM based Agents: Representations, Operations, and Emerging Topics*. arXiv:2505.00675. <https://arxiv.org/abs/2505.00675>
21. Gan T., Sun Q. (2025). *RAG-MCP: Mitigating Prompt Bloat in LLM Tool Selection via Retrieval-Augmented Generation*. arXiv:2505.03275. <https://arxiv.org/abs/2505.03275>
22. Lumer E., Gulati A., Subbiah V. vd. (2025). *ScaleMCP: Dynamic and Auto-Synchronizing Model Context Protocol Tools for LLM Agents*. arXiv:2505.06416. <https://arxiv.org/abs/2505.06416>
23. Tallam K., Miller E. (2025). *Operationalizing CaMeL: Strengthening LLM Defenses for Enterprise Deployment*. arXiv:2505.22852. <https://arxiv.org/abs/2505.22852>
24. Zhang J., Hu S., Lu C. vd. (2025). *Darwin Godel Machine: Open-Ended Evolution of Self-Improving Agents*. arXiv:2505.22954. <https://arxiv.org/abs/2505.22954>
25. Costa M., Köpf B., Kolluri A. vd. (2025). *Securing AI Agents with Information-Flow Control*. arXiv:2505.23643. <https://arxiv.org/abs/2505.23643>
26. Belcak P., Heinrich G., Diao S. vd. (2025). *Small Language Models are the Future of Agentic AI*. arXiv:2506.02153. <https://arxiv.org/abs/2506.02153>
27. Kang J., Ji M., Zhao Z. vd. (2025). *Memory OS of AI Agent*. arXiv:2506.06326. <https://arxiv.org/abs/2506.06326>
28. Barres V., Dong H., Ray S. vd. (2025). *τ²-Bench: Evaluating Conversational Agents in a Dual-Control Environment*. arXiv:2506.07982. <https://arxiv.org/abs/2506.07982>
29. Beurer-Kellner L., Buesser B., Creţu A. vd. (2025). *Design Patterns for Securing LLM Agents against Prompt Injections*. arXiv:2506.08837. <https://arxiv.org/abs/2506.08837>
30. Li H., Liu X., Chiu H. vd. (2025). *DRIFT: Dynamic Rule-Based Defense with Injection Isolation for Securing LLM Agents*. arXiv:2506.12104. <https://arxiv.org/abs/2506.12104>
31. Feng K., McDonald D., Zhang A. (2025). *Levels of Autonomy for AI Agents*. arXiv:2506.12469. <https://arxiv.org/abs/2506.12469>
32. Kuntz T., Duzan A., Zhao H. vd. (2025). *OS-Harm: A Benchmark for Measuring Safety of Computer Use Agents*. arXiv:2506.14866. <https://arxiv.org/abs/2506.14866>
33. Gou B., Huang Z., Ning Y. vd. (2025). *Mind2Web 2: Evaluating Agentic Search with Agent-as-a-Judge*. arXiv:2506.21506. <https://arxiv.org/abs/2506.21506>
34. Chen S., Zharmagambetov A., Wagner D. vd. (2025). *Meta-SecAlign: Training LLMs against Prompt Injection for Robust Agents*. arXiv:2507.02735. <https://arxiv.org/abs/2507.02735>
35. Zhu Y., Jin T., Pruksachatkun Y. vd. (2025). *Establishing Best Practices for Building Rigorous Agentic Benchmarks*. arXiv:2507.02825. <https://arxiv.org/abs/2507.02825>
36. Li Z., Xi C., Li C. vd. (2025). *MemOS: A Memory OS for AI System*. arXiv:2507.03724. <https://arxiv.org/abs/2507.03724>
37. Hu Y., Wang Y., McAuley J. (2025). *Evaluating Memory in LLM Agents via Incremental Multi-Turn Interactions*. arXiv:2507.05257. <https://arxiv.org/abs/2507.05257>
38. Wang Y., Chen X. (2025). *MIRIX: Multi-Agent Memory System for LLM-Based Agents*. arXiv:2507.07957. <https://arxiv.org/abs/2507.07957>
39. Mozannar H., Bansal G., Tan C. vd. (2025). *Magentic-UI: Towards Human-in-the-loop Agentic Systems*. arXiv:2507.22358. <https://arxiv.org/abs/2507.22358>
40. Mo G., Zhong W., Chen J. vd. (2025). *LiveMCPBench: Can Agents Navigate an Ocean of MCP Tools?*. arXiv:2508.01780. <https://arxiv.org/abs/2508.01780>
41. Ma W., Nan J., Wu W. vd. (2025). *What Deserves Memory: Adaptive Memory Distillation for LLM Agents*. arXiv:2508.03341. <https://arxiv.org/abs/2508.03341>
42. Fang R., Liang Y., Wang X. vd. (2025). *Memp: Exploring Agent Procedural Memory*. arXiv:2508.06433. <https://arxiv.org/abs/2508.06433>
43. Wang X., Wang B., Lu D. vd. (2025). *OpenCUA: Open Foundations for Computer-Use Agents*. arXiv:2508.09123. <https://arxiv.org/abs/2508.09123>
44. Luo Z., Shen Z., Yang W. vd. (2025). *MCP-Universe: Benchmarking Large Language Models with Real-World Model Context Protocol Servers*. arXiv:2508.14704. <https://arxiv.org/abs/2508.14704>
45. Wang Z., Gao Y., Wang Y. vd. (2025). *MCPTox: A Benchmark for Tool Poisoning Attack on Real-World MCP Servers*. arXiv:2508.14925. <https://arxiv.org/abs/2508.14925>
46. Yan S., Yang X., Huang Z. vd. (2025). *Memory-R1: Enhancing Large Language Model Agents to Manage and Utilize Memories via Reinforcement Learning*. arXiv:2508.19828. <https://arxiv.org/abs/2508.19828>
47. Wang Z., Chang Q., Patel H. vd. (2025). *MCP-Bench: Benchmarking Tool-Using LLM Agents with Complex Real-World Tasks via MCP Servers*. arXiv:2508.20453. <https://arxiv.org/abs/2508.20453>
48. Froger R., Andrews P., Bettini M. vd. (2025). *ARE: Scaling Up Agent Environments and Evaluations*. arXiv:2509.17158. <https://arxiv.org/abs/2509.17158>
49. Ouyang S., Yan J., Hsu I. vd. (2025). *ReasoningBank: Scaling Agent Self-Evolving with Reasoning Memory*. arXiv:2509.25140. <https://arxiv.org/abs/2509.25140>
50. Wang Y., Takanobu R., Liang Z. vd. (2025). *Mem-α: Learning Memory Construction via Reinforcement Learning*. arXiv:2509.25911. <https://arxiv.org/abs/2509.25911>
51. Gonzalez-Pumariega G., Tu V., Lee C. vd. (2025). *Scaling Agents for Computer Use*. arXiv:2510.02250. <https://arxiv.org/abs/2510.02250>
52. Zhang Q., Hu C., Upasani S. vd. (2025). *Agentic Context Engineering: Evolving Contexts for Self-Improving Language Models*. arXiv:2510.04618. <https://arxiv.org/abs/2510.04618>
53. Nasr M., Carlini N., Sitawarin C. vd. (2025). *The Attacker Moves Second: Stronger Adaptive Attacks Bypass Defenses Against Llm Jailbreaks and Prompt Injections*. arXiv:2510.09023. <https://arxiv.org/abs/2510.09023>
54. Kapoor S., Stroebl B., Kirgis P. vd. (2025). *Holistic Agent Leaderboard: The Missing Infrastructure for AI Agent Evaluation*. arXiv:2510.11977. <https://arxiv.org/abs/2510.11977>
55. Zhang D., Li Z., Luo X. vd. (2025). *MCP Security Bench (MSB): Benchmarking Attacks Against Model Context Protocol in LLM Agents*. arXiv:2510.15994. <https://arxiv.org/abs/2510.15994>
56. Fang J., Deng X., Xu H. vd. (2025). *LightMem: Lightweight and Efficient Memory-Augmented Generation*. arXiv:2510.18866. <https://arxiv.org/abs/2510.18866>
57. Hu Y., Liu S., Yue Y. vd. (2025). *Memory in the Age of AI Agents*. arXiv:2512.13564. <https://arxiv.org/abs/2512.13564>
58. Maloyan N., Namiot D. (2026). *Breaking the Protocol: Security Analysis of the Model Context Protocol Specification and Prompt Injection Vulnerabilities in Tool-Integrated LLM Agents*. arXiv:2601.17549. <https://arxiv.org/abs/2601.17549>
59. Wei L., Peng X., Dong X. vd. (2026). *FadeMem: Biologically-Inspired Forgetting for Efficient Agent Memory*. arXiv:2601.18642. <https://arxiv.org/abs/2601.18642>
60. Ruan J., Xu Z., Peng Y. vd. (2026). *AOrchestra: Automating Sub-Agent Creation for Agentic Orchestration*. arXiv:2602.03786. <https://arxiv.org/abs/2602.03786>
61. Liu Y., Chen Z., Zhang Y. vd. (2026). *"Do Not Mention This to the User": Detecting and Understanding Malicious Agent Skills in the Wild*. arXiv:2602.06547. <https://arxiv.org/abs/2602.06547>
62. Wang Y., Xu F., Lin Z. vd. (2026). *From Assistant to Double Agent: Formalizing and Benchmarking Attacks on OpenClaw for Personalized Local AI Agent*. arXiv:2602.08412. <https://arxiv.org/abs/2602.08412>
63. Holzbauer F., Schmidt D., Gegenhuber G. vd. (2026). *Context Matters: Repository-Aware Security Analysis of the Agent Skill Ecosystem*. arXiv:2603.16572. <https://arxiv.org/abs/2603.16572>
64. Geng J., Neubig G. (2026). *Effective Strategies for Asynchronous Software Engineering Agents*. arXiv:2603.21489. <https://arxiv.org/abs/2603.21489>
65. Suwansathit S., Zhang Y., Gu G. (2026). *A Security Analysis of the OpenClaw AI Agent Framework*. arXiv:2603.27517. <https://arxiv.org/abs/2603.27517>
66. Wei B., Zhang Y., Pan J. vd. (2026). *ClawSafety: "Safe" LLMs, Unsafe Agents*. arXiv:2604.01438. <https://arxiv.org/abs/2604.01438>
67. Wang Y., Gao H., Niu Z. vd. (2026). *A Systematic Security Evaluation of OpenClaw and Its Variants*. arXiv:2604.03131. <https://arxiv.org/abs/2604.03131>
68. Wang Z., Tu H., Zhang L. vd. (2026). *Your Agent, Their Asset: A Real-World Safety Analysis of OpenClaw*. arXiv:2604.04759. <https://arxiv.org/abs/2604.04759>
69. Lin G., Chen C., Chen Z. vd. (2026). *Full-Duplex-Bench-v3: Benchmarking Tool Use for Full-Duplex Voice Agents Under Real-World Disfluency*. arXiv:2604.04847. <https://arxiv.org/abs/2604.04847>
70. Chen C., Zhang Z., Chen Z. vd. (2026). *Comparing Human Oversight Strategies for Computer-Use Agents*. arXiv:2604.04918. <https://arxiv.org/abs/2604.04918>
71. Zhang Y., Wang Y., Zhu Y. vd. (2026). *ClawBench: Can AI Agents Complete Everyday Online Tasks?*. arXiv:2604.08523. <https://arxiv.org/abs/2604.08523>
72. Uddin M., Shubham K., Blanco E. vd. (2026). *From Recall to Forgetting: Benchmarking Long-Term Memory for Personalized Agents*. arXiv:2604.20006. <https://arxiv.org/abs/2604.20006>
73. Shinde S. (2026). *SCM: Sleep-Consolidated Memory with Algorithmic Forgetting for Large Language Models*. arXiv:2604.20943. <https://arxiv.org/abs/2604.20943>
74. Saha S., Faghih K., Feizi S. (2026). *Under the Hood of SKILL.md: Semantic Supply-chain Attacks on AI Agent Skill Registry*. arXiv:2605.11418. <https://arxiv.org/abs/2605.11418>
75. Zhang H., Xu L., Wang Z. vd. (2026). *π-Bench: Evaluating Proactive Personal Assistant Agents in Long-Horizon Workflows*. arXiv:2605.14678. <https://arxiv.org/abs/2605.14678>
76. Cim M., Topcu B., Das C. vd. (2026). *Parallel Context Compaction for Long-Horizon LLM Agent Serving*. arXiv:2605.23296. <https://arxiv.org/abs/2605.23296>
77. Liu S., Tang X., Yang X. vd. (2026). *When the Manual Lies: A Realistic Benchmark to Evaluate MCP Poisoning Attacks for LLM Agents*. arXiv:2605.24069. <https://arxiv.org/abs/2605.24069>
78. Wang P., Li Y., Tian Y. (2026). *Aligning Provenance with Authorization: A Dual-Graph Defense for LLM Agents*. arXiv:2605.26497. <https://arxiv.org/abs/2605.26497>
79. Yang X., Nie L., Chandra E. vd. (2026). *When Parallelism Pays Off: Cohesion-Aware Task Partitioning for Multi-Agent Coding*. arXiv:2606.00953. <https://arxiv.org/abs/2606.00953>
80. Weng X. (2026). *What You Approve Is What Executes: Consent Integrity for Black-Box LLM Agents*. arXiv:2606.02668. <https://arxiv.org/abs/2606.02668>
81. Lv Z., Li Y., Tan Z. vd. (2026). *RealClawBench: Live OpenClaw Benchmarks from Real Developer-Agent Sessions*. arXiv:2606.03889. <https://arxiv.org/abs/2606.03889>
82. Dash P., Ge T., Jain A. vd. (2026). *From Untrusted Input to Trusted Memory: A Systematic Study of Memory Poisoning Attacks in LLM Agents*. arXiv:2606.04329. <https://arxiv.org/abs/2606.04329>
83. Semenov A., Dorofeev S. (2026). *Beyond Compaction: Structured Context Eviction for Long-Horizon Agents*. arXiv:2606.11213. <https://arxiv.org/abs/2606.11213>
84. Sharma T. (2026). *SMSR: Certified Defence Against Runtime Memory Poisoning in Persistent LLM Agent Systems*. arXiv:2606.12703. <https://arxiv.org/abs/2606.12703>
85. Mohammadmirzaei M., Flanigan J. (2026). *OSGuard: A Benchmark for Safety in Computer-Use Agents*. arXiv:2606.15034. <https://arxiv.org/abs/2606.15034>
86. Li T., Zhang J., Jurayj W. vd. (2026). *Self-Compacting Language Model Agents*. arXiv:2606.23525. <https://arxiv.org/abs/2606.23525>
87. Louck Y. (2026). *Securing LLM-Agent Long-Term Memory Against Poisoning: Non-Malleable, Origin-Bound Authority with Machine-Checked Guarantees*. arXiv:2606.24322. <https://arxiv.org/abs/2606.24322>
88. Yang T., Paul S., Srinivasan V. vd. (2026). *TRUSTMEM: Learning Trustworthy Memory Consolidation for LLM Agents with Long-Term Memory*. arXiv:2606.25161. <https://arxiv.org/abs/2606.25161>
89. Yuan M., Zhou Z., Xiong X. vd. (2026). *OSWorld 2.0: Benchmarking Computer Use Agents on Long-Horizon Real-World Tasks*. arXiv:2606.29537. <https://arxiv.org/abs/2606.29537>
90. Torres G., Shrestha S., Misra S. (2026). *When Agents Remember Too Much: Memory Poisoning Attacks on Large Language Model Agents*. arXiv:2607.06595. <https://arxiv.org/abs/2607.06595>
91. Badhe S., Tiwari P. (2026). *Agent Skill Security: Threat Models, Attacks, Defenses, and Evaluation*. arXiv:2607.13987. <https://arxiv.org/abs/2607.13987>
92. Yan T. (2026). *Do User-Authored Permission Policies Improve Protection Against AI Agent Overreach?*. arXiv:2608.27443. <https://arxiv.org/abs/2608.27443>
93. Rusu T., Khanzadeh S., Alalfi M. (2026). *Selective Forgetting: A Graph-Based Memory Framework for Long-Term LLM Agents*. arXiv:2608.28978. <https://arxiv.org/abs/2608.28978>
94. Xiong W., Karanjai R., Lu Y. vd. (2026). *Reachability-Based Capability Confinement for LLM Agents under Indirect Prompt Injection*. arXiv:2608.30041. <https://arxiv.org/abs/2608.30041>
95. Irshad H., Mughees A., Mughees N. vd. (2026). *The Verifiable Action Card: Trustworthy Human-in-the-Loop Control for Secure Autonomous Agents*. arXiv:2609.18411. <https://arxiv.org/abs/2609.18411>
96. Nikolaou E., Eckhoff M., Flood R. vd. (2026). *CaMeLoT: CaMeL orchestrated with Temporal logic for static verification and liveness*. arXiv:2609.18674. <https://arxiv.org/abs/2609.18674>
97. Wang M., Chen H., Guo Y. vd. (2026). *StateComp: Learning When to Compress History in Long Horizon Agents*. arXiv:2609.27298. <https://arxiv.org/abs/2609.27298>
98. Satish R., Sinha P., Kawada A. vd. (2026). *Beyond Token Savings: A Systematic Study of Context Compression in LLM Agents*. arXiv:2609.32961. <https://arxiv.org/abs/2609.32961>
99. Tao Y., Zhong X., Zhao H. vd. (2026). *DyadMem: A Long-Term Memory Benchmark of How Agents Work with Users*. arXiv:2610.03020. <https://arxiv.org/abs/2610.03020>
100. Peters-Gill J., Semler A., Bartsch H. vd. (2026). *Can CaMeLs Talk? Securing Multi-Agent Systems Against Indirect Prompt Injection Attacks*. arXiv:2610.05640. <https://arxiv.org/abs/2610.05640>
101. Muku S. (2026). *Verifying Coordination in Parallel Coding Agents: NP-Bench and a Scheduling Planner*. arXiv:2610.07261. <https://arxiv.org/abs/2610.07261>
102. Li H., Li H., Jiang Z. vd. (2026). *When Sub-Agents Work in Parallel: The Promises and Pitfalls of Dynamic Concurrency in Long-Horizon Coding Tasks*. arXiv:2610.10263. <https://arxiv.org/abs/2610.10263>
103. Xie B., Wang C., Ge G. vd. (2026). *Can Agents Forget When Asked? A Benchmark for Memory Unlearning in LLM Agents*. arXiv:2610.18052. <https://arxiv.org/abs/2610.18052>

### 9.2 arXiv dışı kaynaklar

1. Willison, S. (2023). *The Dual LLM pattern for building AI assistants that can resist prompt injection*. <https://simonwillison.net/2023/Apr/25/dual-llm-pattern/>
2. Willison, S. (2025). *The lethal trifecta for AI agents: private data, untrusted content, and external communication*. <https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/>
3. Hadfield, J. vd. / Anthropic (2025). *How we built our multi-agent research system*. <https://www.anthropic.com/engineering/multi-agent-research-system>
4. Agent Skills. *Specification*. <https://agentskills.io/specification>
5. zedraxa/yusufclaw deposu (SPEC.md, PROGRESS.md, docs/RESEARCH.md, docs/DESIGN-RECOMMENDATIONS.md, packages/*). <https://github.com/zedraxa/yusufclaw>

**Doğrulanmış kaynak sayısı:** 103 arXiv makalesi + 4 arXiv dışı web kaynağı = **107** dış kaynak (proje deposu ayrıca incelendi).

