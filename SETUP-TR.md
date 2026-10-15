# İlk aşama: Gemini ve Groq sohbeti

Bu fork ilk aşamada yalnız metin sohbeti için ayarlanır. Kişilik, araçlar,
ses ve hatırlatmalar daha sonra ele alınır. Upstream: ckw1206/gpt-discord-bot.

1. Discord Developer Portal'da uygulama oluştur; Message Content Intent'i aç.
2. Yalnız hedef test kanalında View Channel, Send Messages ve Read Message
   History izinleri ver. Administrator gerekmez. Botu sunucuya davet et.
3. VDS'te `/etc/discord-ai-bot/env` dosyasını root sahibi ve 600 izinle oluştur:

   ```dotenv
   DISCORD_BOT_TOKEN=
   DISCORD_CLIENT_ID=
   GEMINI_API_KEY=
   GROQ_API_KEY=
   ```

4. `config-chat.yaml` dosyasını `config.yaml` olarak kopyala. Admin ID'sini
   ve seçilen test kanalının ID'sini permissions altında sayısal olarak yaz.
   API anahtarlarını YAML'a veya Git'e koyma.
5. Sağlayıcı hesaplarında ücretsiz planı doğrula; modelleri gerçek hesapta
   test et. Örnek model adları hesabın erişimini garanti etmez. İki model de
   yanıt verdikten sonra istenirse Groq'u fallback listesine ekle.
6. Venv'e requirements.txt yükle, deploy/discord-ai-bot.service birimini
   kur ve servisi başlat. Bu birim secrets dosyasını systemd üzerinden okur.
7. Discord'da botu etiketle; yanıtına Reply ile konuşmayı sürdür. `/model`
   ile iki sağlayıcıyı ayrı dene. Servis yeniden başlatma testi yap.

Özel karakter tanımı bulunmaz; sadece kısa Türkçe yanıt için temel dil
talimatı vardır. Mesaj içeriği başlangıç loguna yazılmaz. Sağlayıcılar
yalnız yapılandırılan iki servisle sınırlıdır; ücretli başka servise geçiş yoktur.
Ücretsiz kullanım seçimi API anahtarının bağlı olduğu hesapta ayrıca doğrulanır.
