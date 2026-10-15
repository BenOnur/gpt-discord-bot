# Discord'a özel ikinci beyin

Motor: [Basic Memory](https://github.com/basicmachines-co/basic-memory), sabit `basic-memory==0.23.2`, ayrı Python ortamı. Yerel sürüm ücretsizdir; AGPL-3.0 lisanslıdır. Basic Memory dosya/not/indeks motorudur; Discord dinleme, geçmiş tarama ve otomatik not çıkarma bu repodaki `bot/memory.py` adaptöründedir. Mevcut bot/kişilik ve Gemini/Groq korunur.

Çalışma yeri yalnız VDS'tir: bot, arşiv, indeks, notlar ve zamanlayıcı sunucudadır. Bilgisayarda çalışan bir parça, Obsidian uygulaması, ağ diski, tünel veya senkronizasyon gerekmez. Discord hafızası Hermes Agent'ın `/home/hermes/Beyin` kasasından ve kişisel Beyin Git deposundan bağımsızdır. `discord-ai` kullanıcısı ve systemd `ProtectHome=true` sınırı Hermes kasasını okuyamaz. Ham mesajlar/notlar bilgisayara veya GitHub'a senkronize edilmez; GitHub yalnız uygulama kodudur.

## Akış

- `memory.guild_ids` sunucularının botça erişilebilen insan mesajları, etiket gerekmeksizin yerel SQLite arşivine yazılır. DM ve bot mesajları alınmaz. `excluded_channel_ids` kapsam dışıdır.
- Başlangıçta ve saatlik geçmiş taranır. Kanal başına ayrı cursor, yeni mesajlarla yarışmadan ve yeniden başlatmalarda kaybolmadan ilerler. Discord hız sınırlarına discord.py uyar. Erişilemeyen/silinmiş geçmiş, botun erişemediği özel thread'ler ve eklerin içeriği kapsam dışıdır.
- Her 5 dakikada bir aynı kanaldaki en çok 80 mesaj, JSON kimlik/tarih/kaynak ekleri dahil yaklaşık 3.500 girdi tokenı bütçesinde Groq tarafından Türkçe özetlenir. O200k tokenizer ve 1.600 çıktı tokenı ile 8.000 TPM kotasında pay bırakılır. Tek başına bütçeyi aşan mesajın analiz alıntısı kısaltılır; tam metni ham arşivde aranabilir. Günlük varsayılan en çok 40 analiz denemesi, UTC gün sınırı. API başarısızsa işaretlenmez; sonraki tur tekrar dener. Ücretsiz sağlayıcı kotası daha düşükse gecikme olabilir.
- Kişiler değişen takma ad yerine Discord ID ile anılır. Notlar kaynak mesaj bağlantılarını içerir. Şaka/küfür gerçek kişisel olgu olarak çıkarılmamalıdır; bu model talimatıdır, hatasızlık garantisi yoktur.
- Basic Memory arşivden çıkan notları `vault/guild-ID/channel-ID/` altında Markdown olarak tutar ve yerel arama indeksine ekler. Obsidian bu vault klasörünü açabilir. Obsidian'ın VDS'te çalışmasına gerek yoktur.
- Bot cevap öncesi ilgili notları, ham arşivdeki FTS5 metin eşleşmelerini ve son kanal mesajlarını getirir. Böylece henüz özetlenmemiş geçmiş de aranabilir. Hafızadaki metin talimat sayılmaz. Notlar sadece soruyu soranın View Channel + Read History erişimi olan kanallardan kullanılabilir. Özel thread üyeliği ayrıca denetlenir.
- Bot yalnız normal mention sohbetinde cevap verir; arka planda dinlemek her mesaja cevap vermek anlamına gelmez. Hafıza dosyaları ve ham arşiv Git'e push edilmez.

## Kurulum / etkinleştirme

1. VDS: `python3 -m venv /opt/discord-memory-venv`, ardından bu ortamda `pip install --pre basic-memory==0.23.2`.
2. `/var/lib/discord-ai-memory` dizinini `discord-ai:discord-ai`, izin `700` olarak oluştur. Servise `ReadWritePaths=/var/lib/discord-ai-memory` ekle. Basic Memory config dizini bu dizinin `config`, notlar `vault` altındadır; kişisel Beyin'e erişmez.
3. Aynı ortam değişkenleriyle `bm project add discord-memory /var/lib/discord-ai-memory/vault --local --default` çalıştır. Discord Developer Portal > Bot > Privileged Gateway Intents > Message Content Intent aç. View Channel/Read Message History izinleri olan kanallar taranabilir. Ek sunucu izinleri verme.
4. Canlı config'te `enable_message_content: true`, `memory.enabled: true`, doğru guild ID'lerini ayarla; servisi yeniden başlat.
5. `/var/lib/discord-ai-memory/archive.sqlite3` içindeki mesaj/analyzed sayısı ve journal'daki `Memory: indexed` kayıtlarıyla ilerleme izlenir. Özel mesaj içerikleri loga yazılmaz.

## Sınırlar

Bu ilk sürüm tam anlamsal profil birleştirme veya unutma arayüzü değildir: kaynaklı dönem notları ve metin araması kullanır. Tüm geçmişi arşivlemek ile ücretsiz kota sınırında analiz etmek farklı ilerler. Silinen Discord mesajları için otomatik geriye dönük not temizleme henüz yoktur. Hafızayı silme/düzeltme yönetimi ayrıca eklenmelidir. Sunucuda kapsama dair görünür bilgilendirme yapılması önerilir; bu adaptör duyuru göndermez.

Testler: `python -m unittest discover -s tests -p test_memory.py`.
