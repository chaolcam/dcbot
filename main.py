import telebot
import os
import random
import threading
import time
from flask import Flask
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import pymongo

# --- GİZLİ AYARLAR (ORTAM DEĞİŞKENLERİNDEN ÇEKİLİR) ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")

# Log grubu ID'sini ortam değişkeninden çeker, yoksa varsayılan 0 yapar
try:
    ADMIN_LOG_GROUP_ID = int(os.environ.get("ADMIN_LOG_GROUP_ID", 0))
except:
    ADMIN_LOG_GROUP_ID = 0

# --- GENEL AYARLAR (AÇIK KALMASINDA SAKINCA OLMAYANLAR) ---
GAME_GROUP_ID = -1004338071438
GAME_THREAD_ID = 1252
VIP_USER_ID = 7075582251 # Ayrıcalıklı Kullanıcı ID'si

# Hata Kontrolü: Eğer Render üzerinde anahtarlar girilmediyse bot başlamadan uyarsın
if not TOKEN or not MONGO_URI:
    print("❌ HATA: TELEGRAM_TOKEN veya MONGO_URI ortam değişkenleri Render üzerinde tanımlanmamış!")
    exit(1)

bot = telebot.TeleBot(TOKEN)

# --- MONGODB ---
db_client = pymongo.MongoClient(MONGO_URI)
db = db_client["oylama_botu_veritabani"]
questions_col = db["dc_sorulari"]   
settings_col = db["dc_ayarlar"]
media_log_col = db["dc_medya_log"]

# --- OYUN HAFIZASI VE YÖNETİCİ DURUMLARI ---
active_games = {}
admin_states = {} 

# --- VARSAYILAN KURALLAR METNİ ---
DEFAULT_RULES = (
    "<b>DOĞRULUK MU CESARET Mİ - OYUN KURALLARI VE ŞARTLARI</b>\n\n"
    "<b>1. Dinamik Oyun Yapısı</b>\n"
    "Sistemde önceden hazırlanmış bir soru havuzu bulunmamaktadır. Şişe çevrildiğinde soruyu sorma hakkı kazanan oyuncu, yönelteceği soruyu veya görevi o an bizzat yazmakla yükümlüdür.\n\n"
    "<b>2. İşlem Süresi Sınırları ve Otomatik İhraç</b>\n"
    "Oyun akışının kesintiye uğramaması amacıyla her hamle için belirli süre sınırları bulunmaktadır. Tanımlanan süre içerisinde hamlesini yapmayan kullanıcılar sistem tarafından otomatik olarak oyundan ihraç edilir.\n\n"
    "<b>3. Soru/Görev Değiştirme Hakkı (Pas)</b>\n"
    "Her katılımcının oyun başına toplam 3 adet \"Pas (Değiştir)\" hakkı bulunmaktadır. Pas hakkı kullanıldığında, soruyu soran kişi yeni bir soru/görev belirlemek zorundadır. Hakları tükenen kullanıcılar mevcut görevi yerine getirmek mecburiyetindedir.\n\n"
    "<b>4. Kategori Seçim Limiti</b>\n"
    "Oyun dengesini sağlamak adına üst üste \"Doğruluk\" seçimi sınırlandırılmıştır. Belirlenen limite ulaşıldığında sistem \"Doğruluk\" seçeneğini kilitler ve kullanıcı \"Cesaret\" kategorisini seçmek zorundadır. Cesaret görevi alındığında seçim sayacı sıfırlanır.\n\n"
    "<b>5. Oda Kurucusu Yetkileri</b>\n"
    "Oyunu <code>/basla</code> komutu ile başlatan kullanıcı \"Oda Kurucusu\" sıfatını alır. Kurucu; lobi bekleme süresini uzatma ve oyun düzenini ihlal eden katılımcıları mesajlarını yanıtlayıp <code>/at</code> komutunu kullanarak oyundan çıkarma yetkisine sahiptir.\n\n"
    "<b>6. Oyuna Katılım ve Ayrılma</b>\n"
    "Oyun seansı başlamış olsa dahi <code>/katil</code> komutu kullanılarak sisteme dahil olmak veya aktif bir oyundan çıkış yapmak için <code>/ayril</code> komutunu kullanmak mümkündür.\n\n"
    "<b>7. Gizlilik ve İfşa Yasağı</b>\n"
    "Oyun esnasında paylaşılan hiçbir görsel, itiraf, hikaye veya yazışma grup dışına çıkarılamaz ve üçüncü şahıslarla paylaşılamaz. Grupta gerçekleşen tüm etkileşimler gruba özel kalmalıdır.\n\n"
    "<b>8. Kişisel Verilerin Korunması</b>\n"
    "Soru sorarken veya görev verirken; diğer oyunculardan telefon numarası, kişisel sosyal medya hesapları, ikametgah adresi veya benzeri özel iletişim bilgileri talep edilmesi kesinlikle yasaktır."
)

# --- AYAR FONKSİYONLARI ---
DEFAULT_SETTINGS = {
    "lobby_time": 15,
    "category_time": 60,
    "question_time": 60,
    "answer_time": 60,
    "truth_limit": 3,
    "rules_text": DEFAULT_RULES
}

def get_settings(chat_id):
    s = settings_col.find_one({"chat_id": chat_id})
    if not s:
        s = DEFAULT_SETTINGS.copy()
        s["chat_id"] = chat_id
        settings_col.insert_one(s)
    return s

def update_setting(chat_id, key, val):
    settings_col.update_one({"chat_id": chat_id}, {"$set": {key: val}}, upsert=True)

def get_settings_menu(chat_id):
    s = get_settings(chat_id)
    text = (
        "⚙️ <b>Oyun Süre ve Kural Ayarları</b>\n\n"
        f"⏳ Lobi Süresi: <b>{s['lobby_time']} sn</b>\n"
        f"⏱ Kategori Seçimi: <b>{s['category_time']} sn</b>\n"
        f"✍️ Soru Yazma: <b>{s['question_time']} sn</b>\n"
        f"🎯 Cevaplama/Görev: <b>{s['answer_time']} sn</b>\n"
        f"🚫 Doğruluk Sınırı: <b>{s.get('truth_limit', 3)} Kez</b>\n\n"
        "Değiştirmek istediğiniz ayarı seçin:"
    )
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton(f"⏳ Lobi ({s['lobby_time']}s)", callback_data="set_edit_lobby"),
        InlineKeyboardButton(f"⏱ Kategori ({s['category_time']}s)", callback_data="set_edit_category")
    )
    markup.add(
        InlineKeyboardButton(f"✍️ Soru ({s['question_time']}s)", callback_data="set_edit_question"),
        InlineKeyboardButton(f"🎯 Cevap ({s['answer_time']}s)", callback_data="set_edit_answer")
    )
    markup.add(
        InlineKeyboardButton(f"🚫 Doğruluk Sınırı ({s.get('truth_limit', 3)} Kez)", callback_data="set_edit_truthlimit")
    )
    markup.add(
        InlineKeyboardButton("📝 Kuralları Düzenle", callback_data="set_edit_rules")
    )
    return text, markup

# --- YARDIMCI FONKSİYONLAR ---
def get_mention(user_id, name):
    return f'<a href="tg://user?id={user_id}">{name}</a>'

def clear_timer(chat_id):
    if chat_id in active_games and "timer" in active_games[chat_id] and active_games[chat_id]["timer"]:
        active_games[chat_id]["timer"].cancel()
        active_games[chat_id]["timer"] = None

def set_timer(chat_id, duration, callback_func):
    clear_timer(chat_id)
    t = threading.Timer(duration, callback_func, args=[chat_id])
    active_games[chat_id]["timer"] = t
    t.start()

def update_lobby_msg(chat_id):
    if chat_id not in active_games: return
    oyun = active_games[chat_id]
    if oyun["status"] != "lobby": return
    
    katilanlar = "\n".join([f"👤 {get_mention(uid, name)}" for uid, name in oyun["players"].items()])
    text = f"🎲 <b>Doğruluk mu Cesaret mi Lobi Açıldı!</b>\n\n<b>Katılanlar:</b>\n{katilanlar}\n\nOynamak isteyenler aşağıdaki butona tıklasın veya <code>/katil</code> yazsın."
    
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton(f"🙋‍♂️ Katıl ({len(oyun['players'])} Kişi)", callback_data="dc_join"),
        InlineKeyboardButton("⏳ +15 Saniye", callback_data="dc_add_time")
    )
    
    try:
        bot.edit_message_text(text, chat_id, oyun["lobby_msg_id"], reply_markup=markup, parse_mode="HTML")
    except Exception as e:
        pass

# --- OYUN MEKANİKLERİ ---
def auto_skip(chat_id):
    if chat_id not in active_games: return
    try:
        oyun = active_games[chat_id]
        status = oyun.get("status")
        kicked_id = None
        
        if status == "category_wait":
            kicked_id = oyun.get("answerer_id") 
        elif status == "waiting_custom_q":
            kicked_id = oyun.get("asker_id")    
        elif status == "playing":
            kicked_id = oyun.get("answerer_id") 
            
        if kicked_id and kicked_id in oyun["players"]:
            kicked_name = oyun["players"].pop(kicked_id)
            kicked_mention = get_mention(kicked_id, kicked_name)
            bot.send_message(chat_id, f"⏱ <b>Süre doldu!</b> {kicked_mention} işlem yapmadığı için oyundan <b>atıldı!</b> Yeni tura geçiliyor...", parse_mode="HTML", message_thread_id=GAME_THREAD_ID)
        else:
            bot.send_message(chat_id, "⏱ <b>Süre doldu!</b> Yeni tura geçiliyor...", parse_mode="HTML", message_thread_id=GAME_THREAD_ID)
        
        next_turn(chat_id)
    except:
        pass

def next_turn(chat_id):
    if chat_id not in active_games: return
    oyun = active_games[chat_id]
    players = oyun["players"]
   
    if len(players) < 2:
        bot.send_message(chat_id, "⚠️ Oyunda yeterli kişi kalmadı. Oyun bitirildi!", message_thread_id=GAME_THREAD_ID)
        del active_games[chat_id]
        return
    
    s = get_settings(chat_id) 
    
    candidates = list(players.keys())
    last_a = oyun.get("last_asker")
    last_b = oyun.get("last_answerer")
    
    available_pairs = [(a, b) for a in candidates for b in candidates if a != b]
    
    if len(candidates) > 2:
        filtered_pairs = [(a, b) for a, b in available_pairs if a != last_a and b != last_b]
        if filtered_pairs:
            available_pairs = filtered_pairs

    weights = []
    for a, b in available_pairs:
        if a == VIP_USER_ID:
            weights.append(60) 
        elif b == VIP_USER_ID:
            weights.append(40) 
        else:
            weights.append(50) 
            
    chosen_pair = random.choices(available_pairs, weights=weights, k=1)[0]
    asker_id, answerer_id = chosen_pair
    
    oyun["last_asker"] = asker_id
    oyun["last_answerer"] = answerer_id
    oyun["asker_id"] = asker_id
    oyun["answerer_id"] = answerer_id
    oyun["status"] = "category_wait"
   
    asker_mention = get_mention(asker_id, players[asker_id])
    answerer_mention = get_mention(answerer_id, players[answerer_id])
    
    truth_streak = oyun["truth_streaks"].get(answerer_id, 0)
    limit = s.get('truth_limit', 3)
   
    text = f"🎲 <b>Şişe Çevrildi!</b>\n\n{asker_mention} soruyor, {answerer_mention} cevaplıyor!\n\n👉 {answerer_mention}, <b>Doğruluk mu Cesaret mi?</b>\n(Süren: {s['category_time']} saniye)"
   
    markup = InlineKeyboardMarkup()
    if truth_streak >= limit:
        markup.add(
            InlineKeyboardButton("🚫 Doğruluk (Limit Doldu)", callback_data="dc_cat_d_limit"),
            InlineKeyboardButton("🔴 Cesaret", callback_data="dc_cat_c")
        )
        text += f"\n\n<i>⚠️ Üst üste {limit} kez Doğruluk seçtiğin için bu tur Cesaret seçmek zorundasın!</i>"
    else:
        markup.add(
            InlineKeyboardButton("🔵 Doğruluk", callback_data="dc_cat_d"),
            InlineKeyboardButton("🔴 Cesaret", callback_data="dc_cat_c")
        )
   
    bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML", message_thread_id=GAME_THREAD_ID)
    set_timer(chat_id, s['category_time'], auto_skip)

def start_lobby(chat_id):
    if chat_id not in active_games: return
    players = active_games[chat_id]["players"]
   
    if len(players) < 2:
        bot.send_message(chat_id, "⚠️ Yeterli katılım sağlanamadı (En az 2 kişi). Oyun iptal edildi!", parse_mode="HTML", message_thread_id=GAME_THREAD_ID)
        del active_games[chat_id]
        return
       
    try:
        bot.edit_message_reply_markup(chat_id, active_games[chat_id]["lobby_msg_id"], reply_markup=None)
    except:
        pass

    bot.send_message(chat_id, "🔥 <b>Yeterli sayıya ulaşıldı! Oyun başlıyor...</b>", parse_mode="HTML", message_thread_id=GAME_THREAD_ID)
    next_turn(chat_id)

# ==========================================
# KOMUTLAR
# ==========================================

@bot.message_handler(commands=['iptal'])
def cmd_iptal(message):
    if message.chat.id != GAME_GROUP_ID or getattr(message, 'message_thread_id', None) != GAME_THREAD_ID:
        try: bot.delete_message(message.chat.id, message.message_id)
        except: pass
        return

    user_id = message.from_user.id
    if user_id in admin_states:
        del admin_states[user_id]
        bot.reply_to(message, "✅ İşlem iptal edildi.")

@bot.message_handler(commands=['kurallar'])
def cmd_kurallar(message):
    if message.chat.id != GAME_GROUP_ID or getattr(message, 'message_thread_id', None) != GAME_THREAD_ID:
        try: bot.delete_message(message.chat.id, message.message_id)
        except: pass
        return
    
    s = get_settings(message.chat.id)
    kurallar_metni = s.get("rules_text", DEFAULT_RULES)
    
    try:
        bot.reply_to(message, kurallar_metni, parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, "⚠️ Kurallar metni formatında bir hata var. Lütfen HTML formatını düzgün kullanarak kuralları yeniden ayarlayın.")

@bot.message_handler(commands=['ayarlar'])
def cmd_ayarlar(message):
    if message.chat.id != GAME_GROUP_ID or getattr(message, 'message_thread_id', None) != GAME_THREAD_ID:
        try: bot.delete_message(message.chat.id, message.message_id)
        except: pass
        return

    chat_id = message.chat.id
    user_id = message.from_user.id
    
    try:
        uye = bot.get_chat_member(chat_id, user_id)
        if uye.status not in ['administrator', 'creator']:
            bot.reply_to(message, "⚠️ Sadece adminler ayarları değiştirebilir!")
            return
    except Exception as e:
        bot.reply_to(message, "⚠️ Yetkiniz doğrulanamadı. Sadece adminler kullanabilir!")
        return

    text, markup = get_settings_menu(chat_id)
    bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML", message_thread_id=GAME_THREAD_ID)

@bot.message_handler(commands=['basla'])
def cmd_basla(message):
    if message.chat.id != GAME_GROUP_ID or getattr(message, 'message_thread_id', None) != GAME_THREAD_ID:
        try: bot.delete_message(message.chat.id, message.message_id)
        except: pass
        return

    chat_id = message.chat.id
    if chat_id in active_games:
        bot.reply_to(message, "⚠️ Bu grupta zaten devam eden bir oyun var!")
        return
       
    user_id = message.from_user.id
    name = message.from_user.first_name
    s = get_settings(chat_id) 
    
    active_games[chat_id] = {
        "status": "lobby",
        "starter_id": user_id, 
        "players": {user_id: name},
        "pass_rights": {user_id: 3},
        "truth_streaks": {user_id: 0},
        "last_asker": None,
        "last_answerer": None,
        "timer": None,
        "current_tur": "",
        "lobby_end_time": time.time() + s["lobby_time"]
    }
   
    msg = bot.send_message(chat_id, "🎲 Lobi Yükleniyor...", parse_mode="HTML", message_thread_id=GAME_THREAD_ID)
    active_games[chat_id]["lobby_msg_id"] = msg.message_id
    
    update_lobby_msg(chat_id)
    set_timer(chat_id, s["lobby_time"], start_lobby)

@bot.message_handler(commands=['katil'])
def cmd_katil(message):
    if message.chat.id != GAME_GROUP_ID or getattr(message, 'message_thread_id', None) != GAME_THREAD_ID:
        try: bot.delete_message(message.chat.id, message.message_id)
        except: pass
        return

    chat_id = message.chat.id
    if chat_id not in active_games:
        bot.reply_to(message, "⚠️ Şu an aktif bir oyun yok. `/basla` yazarak başlatabilirsin.", parse_mode="Markdown")
        return
        
    user_id = message.from_user.id
    name = message.from_user.first_name
    oyun = active_games[chat_id]
    
    if user_id in oyun["players"]:
        bot.reply_to(message, "⚠️ Zaten oyundasın!")
        return
        
    oyun["players"][user_id] = name
    oyun["pass_rights"][user_id] = 3
    oyun["truth_streaks"][user_id] = 0
    
    bot.send_message(chat_id, f"📥 <b>{name}</b> oyuna katıldı!", parse_mode="HTML", message_thread_id=GAME_THREAD_ID)
    
    if oyun["status"] == "lobby":
        update_lobby_msg(chat_id)
        bot.delete_message(chat_id, message.message_id)

@bot.message_handler(commands=['ayril'])
def cmd_ayril(message):
    if message.chat.id != GAME_GROUP_ID or getattr(message, 'message_thread_id', None) != GAME_THREAD_ID:
        try: bot.delete_message(message.chat.id, message.message_id)
        except: pass
        return

    chat_id = message.chat.id
    if chat_id not in active_games:
        return
        
    oyun = active_games[chat_id]
    user_id = message.from_user.id
    
    if user_id not in oyun["players"]:
        bot.reply_to(message, "⚠️ Zaten aktif oyun kadrosunda değilsiniz.")
        return
        
    name = oyun["players"].pop(user_id)
    
    bot.send_message(chat_id, f"🚶 <b>{name}</b> oyundan ayrıldı.", parse_mode="HTML", message_thread_id=GAME_THREAD_ID)
    
    if oyun["status"] == "lobby":
        update_lobby_msg(chat_id)
    else:
        if user_id in [oyun.get("asker_id"), oyun.get("answerer_id")]:
            clear_timer(chat_id)
            bot.send_message(chat_id, "⚠️ Aktif turdaki oyunculardan biri ayrıldığı için yeni tur başlatılıyor...", parse_mode="HTML", message_thread_id=GAME_THREAD_ID)
            next_turn(chat_id)
        elif len(oyun["players"]) < 2:
            clear_timer(chat_id)
            bot.send_message(chat_id, "⚠️ Oyunda yeterli kişi kalmadı. Oyun bitirildi!", message_thread_id=GAME_THREAD_ID)
            del active_games[chat_id]

@bot.message_handler(commands=['at'])
def cmd_at(message):
    if message.chat.id != GAME_GROUP_ID or getattr(message, 'message_thread_id', None) != GAME_THREAD_ID:
        try: bot.delete_message(message.chat.id, message.message_id)
        except: pass
        return

    chat_id = message.chat.id
    if chat_id not in active_games:
        return
        
    oyun = active_games[chat_id]
    user_id = message.from_user.id
    
    if user_id != oyun.get("starter_id"):
        bot.reply_to(message, "⚠️ Sadece oyunu başlatan kişi `/at` komutunu kullanabilir!", parse_mode="Markdown")
        return
        
    if not message.reply_to_message:
        bot.reply_to(message, "⚠️ Lütfen oyundan atmak istediğiniz kişinin herhangi bir mesajını yanıtlayarak `/at` yazın.", parse_mode="Markdown")
        return
        
    target_id = message.reply_to_message.from_user.id
    if target_id == user_id:
        bot.reply_to(message, "⚠️ Kendini oyundan atamazsın!")
        return
        
    if target_id in oyun["players"]:
        kicked_name = oyun["players"].pop(target_id)
        bot.reply_to(message, f"👢 <b>{kicked_name}</b> adlı kullanıcı oyun kurucusu tarafından oyundan atıldı!", parse_mode="HTML")
        if oyun["status"] == "lobby":
            update_lobby_msg(chat_id)
        else:
            if target_id in [oyun.get("asker_id"), oyun.get("answerer_id")]:
                clear_timer(chat_id)
                next_turn(chat_id)
    else:
        bot.reply_to(message, "⚠️ Bu kişi zaten oyunda değil.")

@bot.message_handler(commands=['bitir'])
def cmd_bitir(message):
    if message.chat.id != GAME_GROUP_ID or getattr(message, 'message_thread_id', None) != GAME_THREAD_ID:
        try: bot.delete_message(message.chat.id, message.message_id)
        except: pass
        return

    chat_id = message.chat.id
    if chat_id not in active_games:
        bot.reply_to(message, "⚠️ Şu an devam eden bir oyun yok.")
        return
   
    oyun = active_games[chat_id]
    user_id = message.from_user.id
    is_authorized = False
    
    if user_id == oyun.get("starter_id"):
        is_authorized = True
    else:
        try:
            uye = bot.get_chat_member(chat_id, user_id)
            if uye.status in ['administrator', 'creator']:
                is_authorized = True
        except:
            pass
            
    if not is_authorized:
        bot.reply_to(message, "⚠️ Sadece adminler veya oyunu başlatan kişi oyunu bitirebilir!")
        return
    
    clear_timer(chat_id)
    del active_games[chat_id]
    bot.reply_to(message, "🛑 <b>Oyun sonlandırıldı!</b> Her şey sıfırlandı.", parse_mode="HTML")

# ==========================================
# MEDYA LOGLAMA SİSTEMİ (Gizli Admin Güvenliği)
# ==========================================
@bot.message_handler(func=lambda message: True, content_types=['photo', 'video', 'animation', 'video_note', 'document'])
def handle_media_logging(message):
    if message.chat.id != GAME_GROUP_ID or getattr(message, 'message_thread_id', None) != GAME_THREAD_ID:
        return
        
    if not ADMIN_LOG_GROUP_ID:
        return # Log grubu ayarlanmadıysa işlem yapma

    user_id = message.from_user.id
    name = message.from_user.first_name
    mention = get_mention(user_id, name)
    
    media_log_col.insert_one({
        "user_id": user_id,
        "name": name,
        "zaman": time.time(),
        "medya_tipi": message.content_type
    })
    
    caption_text = (
        f"🚨 <b>YENİ MEDYA LOGU</b>\n"
        f"👤 Gönderen: {mention} (<code>{user_id}</code>)\n"
        f"📍 Konum: Doğruluk mu Cesaret mi Odası"
    )
    
    try:
        if message.content_type == 'photo':
            bot.send_photo(ADMIN_LOG_GROUP_ID, message.photo[-1].file_id, caption=caption_text, parse_mode="HTML")
        elif message.content_type == 'video':
            bot.send_video(ADMIN_LOG_GROUP_ID, message.video.file_id, caption=caption_text, parse_mode="HTML")
        elif message.content_type == 'animation':
            bot.send_animation(ADMIN_LOG_GROUP_ID, message.animation.file_id, caption=caption_text, parse_mode="HTML")
        elif message.content_type == 'video_note':
            bot.send_video_note(ADMIN_LOG_GROUP_ID, message.video_note.file_id)
            bot.send_message(ADMIN_LOG_GROUP_ID, caption_text, parse_mode="HTML")
        elif message.content_type == 'document':
            bot.send_document(ADMIN_LOG_GROUP_ID, message.document.file_id, caption=caption_text, parse_mode="HTML")
    except Exception as e:
        pass 

# ==========================================
# METİN (SORU YAZMA VE KURAL DÜZENLEME) YAKALAMA
# ==========================================
@bot.message_handler(func=lambda message: True, content_types=['text'])
def handle_text_messages(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    if user_id in admin_states and admin_states[user_id]["action"] == "editing_rules" and chat_id == admin_states[user_id]["chat_id"]:
        new_rules = message.text
        update_setting(chat_id, "rules_text", new_rules)
        del admin_states[user_id]
        bot.reply_to(message, "✅ <b>Yeni kurallar başarıyla kaydedildi!</b>\n\nGösterimi test etmek için <code>/kurallar</code> yazabilirsiniz.", parse_mode="HTML")
        return

    if chat_id != GAME_GROUP_ID or getattr(message, 'message_thread_id', None) != GAME_THREAD_ID:
        return
        
    if chat_id not in active_games:
        return
        
    oyun = active_games[chat_id]
    
    if oyun.get("status") == "waiting_custom_q" and user_id == oyun.get("asker_id"):
        clear_timer(chat_id)
        
        secilen_soru = message.text.strip()
        tur = oyun["current_tur"]
        
        questions_col.insert_one({"tur": tur, "metin": secilen_soru})
        s = get_settings(chat_id)
        
        asker_mention = get_mention(oyun["asker_id"], oyun["players"][oyun["asker_id"]])
        answerer_mention = get_mention(oyun["answerer_id"], oyun["players"][oyun["answerer_id"]])
        tur_yazi = "DOĞRULUK ZAMANI" if tur == "d" else "CESARET ZAMANI"
        
        text = f"🔵 <b>{tur_yazi}!</b>\n{asker_mention} sordu, {answerer_mention} cevaplıyor!\n\n❓ <b>Soru/Görev:</b> <i>{secilen_soru}</i>\n\n(Cevaplamak için {s['answer_time']} saniyen var)"
        
        kalan_hak = oyun["pass_rights"].get(oyun["answerer_id"], 0)
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton(f"🔄 Değiştir ({kalan_hak} Hak)", callback_data="dc_change"),
            InlineKeyboardButton("🎲 Sıradaki Tur", callback_data="dc_next")
        )
        
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML", message_thread_id=GAME_THREAD_ID)
        
        oyun["status"] = "playing"
        set_timer(chat_id, s['answer_time'], auto_skip)

# ==========================================
# BUTON İŞLEMLERİ (Ayar ve Oyun Butonları)
# ==========================================
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    chat_id = call.message.chat.id
    user_id = call.from_user.id
    data = call.data
    oyun = active_games.get(chat_id)

    if data.startswith("set_"):
        try:
            uye = bot.get_chat_member(chat_id, user_id)
            if uye.status not in ['administrator', 'creator']:
                bot.answer_callback_query(call.id, "Sadece adminler ayarları değiştirebilir!", show_alert=True)
                return
        except Exception as e:
            bot.answer_callback_query(call.id, "Yetkiniz doğrulanamadı. İşlem reddedildi!", show_alert=True)
            return

        if data == "set_main":
            text, markup = get_settings_menu(chat_id)
            bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=markup, parse_mode="HTML")
            return
            
        elif data == "set_edit_rules":
            admin_states[user_id] = {"action": "editing_rules", "chat_id": chat_id}
            bot.answer_callback_query(call.id)
            text = (
                "📝 <b>Kuralları Düzenleme Modu</b>\n\n"
                "Lütfen oyun için yeni kuralları <b>tek bir mesaj halinde</b> buraya yazıp gönderin.\n\n"
                "<i>İpucu: Kalın yazmak için metni HTML etiketleri arasına alabilirsiniz. İşlemi iptal etmek için /iptal yazın.</i>"
            )
            bot.send_message(chat_id, text, parse_mode="HTML", message_thread_id=GAME_THREAD_ID)
            return

        elif data.startswith("set_edit_"):
            key = data.split("_")[2]
            options = {
                "lobby": [15, 30, 45, 60],
                "category": [30, 45, 60, 90],
                "question": [30, 60, 90, 120],
                "answer": [30, 60, 90, 120, 180],
                "truthlimit": [1, 2, 3, 4, 5, 10]
            }
            labels = {
                "lobby": "Lobi Bekleme Süresi",
                "category": "Kategori Seçme Süresi",
                "question": "Soru Yazma Süresi",
                "answer": "Cevaplama Süresi",
                "truthlimit": "Üst Üste Doğruluk Limiti"
            }
            
            markup = InlineKeyboardMarkup(row_width=3 if key == "truthlimit" else 2)
            row = []
            for val in options[key]:
                suffix = "kez" if key == "truthlimit" else "saniye"
                row.append(InlineKeyboardButton(f"{val} {suffix}", callback_data=f"set_val_{key}_{val}"))
            markup.add(*row)
            markup.add(InlineKeyboardButton("🔙 Geri Dön", callback_data="set_main"))
            
            text = f"⚙️ <b>Ayarlar ➔ {labels[key]}</b>\n\nLütfen yeni değeri seçin:"
            bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=markup, parse_mode="HTML")
            return

        elif data.startswith("set_val_"):
            parts = data.split("_")
            key_base = parts[2]
            
            if key_base == "truthlimit":
                key = "truth_limit"
            else:
                key = key_base + "_time"
                
            val = int(parts[3])
            
            update_setting(chat_id, key, val)
            bot.answer_callback_query(call.id, "Ayar başarıyla güncellendi!")
            
            text, markup = get_settings_menu(chat_id)
            bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=markup, parse_mode="HTML")
            return

    # --- OYUN İŞLEMLERİ ---
    if data == "dc_join":
        if chat_id not in active_games or oyun["status"] != "lobby":
            bot.answer_callback_query(call.id, "Oyun çoktan başlamış veya bitmiş!")
            return
        if user_id in oyun["players"]:
            bot.answer_callback_query(call.id, "Zaten katıldınız!")
            return
           
        oyun["players"][user_id] = call.from_user.first_name
        oyun["pass_rights"][user_id] = 3
        oyun["truth_streaks"][user_id] = 0
       
        bot.send_message(chat_id, f"📥 <b>{call.from_user.first_name}</b> oyuna katıldı!", parse_mode="HTML", message_thread_id=GAME_THREAD_ID)
        
        update_lobby_msg(chat_id)
        bot.answer_callback_query(call.id, "Oyuna katıldınız!")
        return

    if data == "dc_add_time":
        if not oyun or oyun["status"] != "lobby":
            bot.answer_callback_query(call.id, "Lobi kapalı.")
            return
        if user_id != oyun.get("starter_id"):
            bot.answer_callback_query(call.id, "Sadece oyunu başlatan kişi süre ekleyebilir!", show_alert=True)
            return
            
        oyun["lobby_end_time"] += 15
        kalan = max(1, oyun["lobby_end_time"] - time.time())
        set_timer(chat_id, kalan, start_lobby)
        bot.answer_callback_query(call.id, "+15 Saniye Eklendi!")
        return

    if not oyun:
        bot.answer_callback_query(call.id, "Aktif oyun bulunmuyor.")
        return

    if data == "dc_cat_d_limit":
        if user_id != oyun.get("answerer_id"):
            bot.answer_callback_query(call.id, "Sıra sende değil!", show_alert=True)
            return
            
        limit = get_settings(chat_id).get('truth_limit', 3)
        bot.answer_callback_query(call.id, f"Üst üste {limit} kez Doğruluk seçtin! Artık Cesaret seçmelisin.", show_alert=True)
        return

    if data in ["dc_cat_d", "dc_cat_c"]:
        if user_id != oyun.get("answerer_id"):
            bot.answer_callback_query(call.id, "Sıra sende değil!", show_alert=True)
            return
           
        tur = "d" if data == "dc_cat_d" else "c"
        tur_yazi = "Doğruluk" if tur == "d" else "Cesaret"
        
        if tur == "d":
            oyun["truth_streaks"][user_id] = oyun["truth_streaks"].get(user_id, 0) + 1
        else:
            oyun["truth_streaks"][user_id] = 0
            
        oyun["current_tur"] = tur
        s = get_settings(chat_id)
       
        asker_mention = get_mention(oyun["asker_id"], oyun["players"][oyun["asker_id"]])
       
        text = f"✍️ {asker_mention}, lütfen sormak istediğin <b>{tur_yazi}</b> sorusunu veya görevi <b>şimdi bu sohbete yaz!</b>\n\nSüren: {s['question_time']} saniye"
        
        bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=None, parse_mode="HTML")
        oyun["status"] = "waiting_custom_q"
        set_timer(chat_id, s['question_time'], auto_skip)
        return

    if data == "dc_change":
        if user_id != oyun.get("answerer_id"):
            bot.answer_callback_query(call.id, "Sadece cevap veren kullanabilir!", show_alert=True)
            return
        if oyun["pass_rights"].get(user_id, 0) <= 0:
            bot.answer_callback_query(call.id, "Pas hakkın kalmadı!", show_alert=True)
            return
           
        oyun["pass_rights"][user_id] -= 1
        s = get_settings(chat_id)
        
        tur_yazi = "Doğruluk" if oyun["current_tur"] == "d" else "Cesaret"
        asker_mention = get_mention(oyun["asker_id"], oyun["players"][oyun["asker_id"]])
        answerer_mention = get_mention(oyun["answerer_id"], oyun["players"][oyun["answerer_id"]])
        
        text = f"🔄 <b>PAS KULLANILDI!</b>\n\n✍️ {asker_mention}, {answerer_mention} pas hakkını kullandı. Lütfen <b>YENİ</b> bir <b>{tur_yazi}</b> sorusu/görevi yaz!\n\nSüren: {s['question_time']} saniye"
        
        bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=None, parse_mode="HTML")
        oyun["status"] = "waiting_custom_q"
        set_timer(chat_id, s['question_time'], auto_skip)
        
        bot.answer_callback_query(call.id, "Pas kullandın! Yeni soru isteniyor...")
        return

    if data == "dc_next":
        if user_id not in [oyun.get("asker_id"), oyun.get("answerer_id")]:
            bot.answer_callback_query(call.id, "Sadece soruyu soran veya cevaplayan yeni tura geçebilir!", show_alert=True)
            return
            
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
        bot.answer_callback_query(call.id, "Yeni tura geçiliyor...")
        next_turn(chat_id)

# --- Web Server ---
app = Flask(__name__)
@app.route('/')
def home():
    return "DC Botu Aktif!"

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = threading.Thread(target=run)
    t.start()

if __name__ == "__main__":
    keep_alive()
    print("Doğruluk mu Cesaret mi Botu Başlatıldı!")
    bot.infinity_polling()
