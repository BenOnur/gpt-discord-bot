# Discord ortak asistanı

Botun hafızası ve skill'leri VDS'te `/var/lib/discord-ai-memory` altında yaşar.
Kişisel Avenox/Hermes beyniyle bağlantı, senkron veya ortak kullanıcı verisi yoktur.
Model ağırlıkları eğitilmez. Öğrenme kaynaklı kayıtlar, düzeltmeler ve tekrar
kullanılabilir prosedürlerin birikmesidir.

## Konuşarak kullanım

- `@bot şu kuralı kaydet: ciddi bir soru sorulduğunda önce araştır sonra net cevap ver`
- `@bot bundan sonra benim için teknik açıklamalarda örnek göster`
- `@bot skill oluştur: oyun planı istersem önce uygun saatleri sor sonra tek bir plan çıkar`
- `@bot hafızanı göster` / `@bot skilllerini göster`
- `@bot 12 numaralı kaydı düzelt ...` / `@bot 12 numaralı kaydı unut`
- `@bot şu an dinlediğim şarkı hakkında araştırma yap`
- `@bot hatırlatmalarımı göster` / `@bot 3 numaralı hatırlatmayı iptal et`
- `@bot hafıza durumu`

Açık kayıt komutları mümkün olduğunca model çağrısını beklemeden kalıcı yazılır.
Daha serbest cümleleri model `manage_memory` aracıyla işler. Kayıtlar kimlik,
kaynak mesaj, revizyon ve kapsam taşır. Tekrar denenmiş aynı kaynak mesaj yeni
kayıt veya ikinci hatırlatıcı oluşturmaz.

Herkes kişisel kayıt/skill oluşturabilir. Ortak kurallar ve ortak skill'ler
yalnız `permissions.users.admin_ids` tarafından oluşturulur/değiştirilir.
`/tepkiler` yetkisi genel asistan yöneticiliği anlamına gelmez.
Herkese açık kanalda kaydedilen kişisel tercih, aynı kişinin diğer uygun
kanallardaki sorularına bağlanabilir. Özel kanal bilgisi dışarı taşınmaz.

Skill'ler çalıştırılabilir kod değildir. Mevcut izinli araçlara ilişkin metinsel
prosedürlerdir. Aynı kanalda iki farklı istekte başarılı görünen araç dizisi
tekrarlandığında otomatik skill üretilir. Herkese açık kanalda üyeler birlikte
bu sayaca katkı verir; yalnız genel araç sırası ortak skill'e dönüşür, kişisel
istek metni/argümanları kopyalanmaz. Özel kanalda kişisel kalır. Bu bir sezgiseldir;
gerçek dışı başarı veya yanlış prosedür görülürse ilgili kayıt düzeltilebilir
veya etkin hafızadan çıkarılabilir. Eski argümanlar yeni göreve kopyalanmaz.
Model yalnız o istek için sunulan araçları çağırabilir.

## Hafıza ve görsel arşivi

Ham mesajlar aranabilir arşive girer. Dört güncel analiz partisine karşılık bir
eski arşiv partisi işlenir; yeni kararlar yıllar önceki backlog'u beklemez.
Basic Memory için isteğe bağlı `memory.hybrid_search` metin/anlam aramasını açar.
Yerel embedding modeli ayrıca hazırlanıp indeks yeniden oluşturulmalıdır.
`discord-memory-retrieval.service` Basic Memory aramasını sıcak tutar; yalnız
127.0.0.1:8801 üzerinde arama/sağlık sorgusu sunar. Bot `memory.retrieval_port`
ile bağlanır; kanal/kişi filtreleri botta tekrar uygulanır. Servis yoksa ham
arşiv ve açık asistan kayıtları çalışmaya devam eder.

`memory.image_index_enabled`, geçmiş/gelecek görsellerin metadata taramasını ve
sınırlı görsel açıklaması/OCR işini açar. `max_image_calls_per_day` varsayılan 5.
Orijinaller topluca kopyalanmaz; aday görselin güncel Discord mesajı yeniden
alınır. İndeks eksiktir; eşleşme bulunamaması hiç görsel olmadığı anlamına gelmez.
Kimlik/yüz tanıma veya görüntüden ilişki durumu çıkarımı yapılmaz.

Mesaj silinince ham metin ve görsel indeksi silinir; etkilenmiş batch notları
geçersiz kılınır ve kalan kaynaklar tekrar analiz kuyruğuna alınır. Açık kayıtlar
etkin hafızadan çıkarılır. Bu, geçmiş değişiklik kayıtlarını veya daha önce
alınmış yedekleri geriye dönük silmez. Düzenlemeler eski özetleri geçersiz kılar.

## Etkinlik ve güvenilir eylemler

Developer Portal Presence + Server Members izinleri ve config'te
`enable_members: true`, `presence.enabled: true` birlikte gereklidir.
Görünür Spotify başlığı/sanatçı/parça URL'si anlık sorgulanır. Oyun ve parça
geçişleri 30 gün/1000 satırlık kompakt geçmişe alınır. İzlemeye başlanmadan
önceki etkinlik geçmişi alınamaz; paylaşılmayan etkinlik görülmez.

Hatırlatma durumları pending/sending/sent/failed/cancelled'dır. Başarısız
gönderim tamamlandı sayılmaz; en çok üç teslim denemesi yapılır. Başarılı
Discord gönderimiyle veritabanı güncellemesi arasında süreç ölürse tam olarak
bir kez teslim garantisi yoktur. Listeleme/iptal/yeniden zamanlama sahibine ve
kanalına göre sınırlandırılır. ISO saatte dilim yoksa Europe/Istanbul kullanılır.

Arşiv/profil/wiki/taklit/trivia okuyucuları ortak kapsam uygular; eski kaynağı
belirsiz dossier önbellekleri kullanılmaz. Özel kaynaklar genel kanalda görünmez.
URL okuyucu public IP'ye DNS sabitlemesi yapar, iç ağları ve yönlendirme
kaçışlarını engeller, gövdeyi sınırlar.

Groq sohbet/arka plan işleri ortak dakika bütçesi kullanır. Aktif sohbete
öncelik verilir; arka plan sonraki döngüye kalır. Bu yerel bütçe başka
uygulamaların aynı API hesabındaki kullanımını göremez. Sağlayıcı kotası yine
aşılabilir; farklı sağlayıcı fallback'i korunur.

## Yedek ve geri yükleme

Snapshot artık asistan DB/notlarını, tepki JSON'unu, Presence DB'sini ve sırları
çıkarılmış bot ayarını da içerir. API anahtarları ayrı tutulur.
`/etc/discord-ai-bot/backup.key` mevcutsa yeni dosyalar AES-256-GCM ile `.enc`
olarak şifrelenir. Anahtar 32 rastgele bayttır ve yedeğin içine konmaz.
Yedek dosyaları önceki kurulumdaki günlük Telegram hedefine gönderilmeye devam
eder; bu botun canlı beyninin başka beyinle bağlandığı anlamına gelmez.
Kurtarma anahtarının ayrıca güvenli biçimde saklanması gerekir; VDS ve tek
anahtar birlikte kaybedilirse şifreli arşiv açılamaz.

Yeni yedek göndermeden geri yükleme provası:

```sh
.venv/bin/python scripts/check_restore.py /path/backup.tar.gz
.venv/bin/python scripts/check_restore.py /path/backup.tar.gz.enc /etc/discord-ai-bot/backup.key
```

Prova ayrı geçici dizine çıkarır; canlı veriyi değiştirmez. SQLite quick_check
doğrular, gerçek kullanıcı senaryolarının tümünü veya çoklu DB atomikliğini
garanti etmez. Açık prosedürler otomatik kod yükseltme/deploy yetkisi vermez.

## ChatGPT abonelik bağlantısı (isteğe bağlı)

### Arşivden gelişen konuşma tarzı

`bot/group_voice.py` erişim izni olan kaynaklardan son 1500 mesaj ve önceki beş
yıldan yılda en çok 100 mesaj örnekler. Kişi başına 150 mesaj sınırıyla grubun
konuşma ritmi, kısa mesajları, küçük harf alışkanlığı ve yaygın argo ifadeleri
çıkarılır. Ham mesajlar veya kişi profilleri üslup dosyasına kopyalanmaz.
Küçük özet `memory.data_dir/voice/` altında erişim kapsamına göre ayrı tutulur;
bir saat sonra gelen ilk istekte yenilenir. Ek model çağrısı yapmaz. Bu katman
model ağırlığı eğitmez; kaynaklı grup hafızası ve ana persona ile birlikte çalışır.
Ciddi görev ile karşılıklı makara ayrılır; küfür tek başına sataşma sayılmaz.

### Abonelik girişi

`scripts/chatgpt_plan_login.py --credentials /var/lib/discord-ai-memory/auth/chatgpt.json`
resmî Sign in with ChatGPT akışını başlatır. VDS loopback callback portu 1456 için
giriş sırasında SSH tüneli gerekir; sonrasında PC bağlantısı gerekmez. Ayrı giriş
kimliği/host ve 0600 token dosyası kullanılır, Hermes tokenları kopyalanmaz.
Abonelik erişimi ancak başarılı gerçek istekle doğrulanmış sayılır.

Provider ayarı: `transport: chatgpt_plan`, `base_url: https://api.openai.com/v1`,
`credentials_path` yukarıdaki dosya; `allowed_guild_ids` yalnız yetkili sunucular.
Discord dışı arka plan işleri bu hesaptan otomatik harcama yapmaz.
Bu adaptör yalnız `gpt-6-luna`, `reasoning.effort: low` kullanır; abonelik yolunda
başka modele otomatik geçilmez. Mevcut bot araçları Responses function çağrılarına
çevrilir; hafıza/kişilik Discord'a özel kalır. Kota ortak hesaptan tüketilir.

Kaynak: https://developers.openai.com/siwc/token-sharing-open-source/sign-in
