"""Small relevant tool menus instead of sending every schema on every turn."""
from bot.assistant_memory import normalize


def select_tools(text, available):
    text = normalize(text)
    selected = {"manage_memory", "web_search", "get_current_time"}
    groups = [
        (("hatirlat", "etiketle", "ertele", "dakika", "saat sonra"), ("set_reminder", "manage_reminders")),
        (("dinle", "sarki", "spotify", "oyun", "oynuyor", "katildi", "sunucuya"), ("get_member_info",)),
        (("fotograf", "avatar", "profil resmi"), ("send_profile_avatar",)),
        (("kimdedi", "kim dedi", "oyun oynat", "bunu kim soyledi"), ("server_trivia",)),
        (("quote", "alinti", "soz karti"), ("generate_quote",)),
        (("https", "link", "video", "tweet"), ("read_url",)),
        (("gecmis", "kim soyledi", "hatirliyor", "ne konus", "hangi mesaj", "eskiden", "arsiv"), ("search_server_history", "server_wiki")),
        (("gorsel", "resim", "fotograf", "ekran goruntusu"), ("search_media",)),
        (("profil", "analiz", "karakter", "kisilik", "beni anlat"), ("get_user_profile", "analyze_personality")),
        (("ne kacirdim", "yokken", "olanlari", "gunun ozeti"), ("catch_me_up", "daily_recap")),
        (("hava", "sicaklik"), ("get_weather",)),
        (("hesapla", "kac eder", "islem"), ("calculate",)),
        (("taklit", "gibi konus"), ("impersonate_user",)),
        (("sese gel", "sesli", "seste"), ("voice_speak",)),
        (("tepki", "emoji"), ("react_to_message",)),
        (("celiski", "yalan", "oyle demedim"), ("fact_check",)),
        (("mahkeme", "hakli", "hakem"), ("drama_judge",)),
        (("dolar", "fiyat", "bitcoin", "hisse"), ("get_market_prices",)),
    ]
    for terms, tools in groups:
        if any(term in text for term in terms):
            selected.update(tools)
    return [tool for tool in available if tool in selected]
