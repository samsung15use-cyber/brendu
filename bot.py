import os
import json
import datetime
import random
import string
import hashlib
import threading
import traceback
import sys
sys.setrecursionlimit(10000)
import time
from functools import lru_cache
from collections import defaultdict
from telebot import TeleBot, types

db_lock = threading.Lock()

# ================= CONFIG =================

TOKEN = '7968143914:AAGBKqmulem7iSNRSGTLaGsB1vGTInEr8v0'
ADMIN_ID = 1417003901
DB_FILE = 'data/db.json'
BOT_USERNAME = 'FastRandom_Robot'

bot = TeleBot(TOKEN)

# ================= КЭШИРОВАННАЯ БАЗА ДАННЫХ =================

class CachedDB:
    """Кэшированная база данных"""
    def __init__(self):
        self._cache = None
        self._cache_time = 0
        self._cache_ttl = 2  # Кэш на 2 секунды
        self._lock = threading.RLock()
        self._write_buffer = []
        self._flush_interval = 1  # Секунд между сохранениями
        self._flush_timer = None
    
    def get(self):
        with self._lock:
            now = time.time()
            if self._cache is None or (now - self._cache_time) > self._cache_ttl:
                self._cache = self._load()
                self._cache_time = now
            return self._cache
    
    def _load(self):
        if not os.path.exists('data'):
            os.makedirs('data')
        if not os.path.isfile(DB_FILE):
            default = {"users": {}, "giveaways": {}, "user_channels": {}}
            with open(DB_FILE, 'w', encoding='utf-8') as f:
                json.dump(default, f, indent=4, ensure_ascii=False)
            return default
        
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def update(self, updater_func):
        """Атомарное обновление"""
        with self._lock:
            db = self.get()
            result = updater_func(db)
            self._write_to_buffer(db)
            return result
    
    def _write_to_buffer(self, db):
        self._write_buffer.append(db)
        if self._flush_timer is None:
            self._flush_timer = threading.Timer(self._flush_interval, self._flush)
            self._flush_timer.start()
    
    def _flush(self):
        with self._lock:
            if self._write_buffer:
                latest = self._write_buffer[-1]
                # Атомарная запись через временный файл
                temp_file = DB_FILE + '.tmp'
                try:
                    with open(temp_file, 'w', encoding='utf-8') as f:
                        json.dump(latest, f, indent=4, ensure_ascii=False)
                    os.replace(temp_file, DB_FILE)
                except Exception as e:
                    print(f"Ошибка сохранения: {e}")
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
                self._write_buffer.clear()
            self._flush_timer = None

# Инициализируем кэшированную БД
cached_db = CachedDB()
db = cached_db.get()

temp_data = {}

# ================= СТАРЫЕ ФУНКЦИИ ДЛЯ СОВМЕСТИМОСТИ =================

def load_db():
    """Совместимость со старым кодом"""
    return cached_db.get()

def save_db(data: dict):
    """Совместимость со старым кодом"""
    cached_db.update(lambda db: data)

def create_colored_button(text, callback_data=None, url=None, color="default"):
    """Создает кнопку с указанным цветом"""
    color_map = {
        "default": None,
        "primary": "primary",   # синий
        "danger": "danger",     # красный
        "success": "success"    # зеленый
    }
    
    if url:
        return types.InlineKeyboardButton(
            text=text,
            url=url,
            style=color_map.get(color)
        )
    else:
        return types.InlineKeyboardButton(
            text=text,
            callback_data=callback_data,
            style=color_map.get(color)
        )

# ================= УДАЛЕНИЕ СООБЩЕНИЙ =================

last_messages = {}

def delete_message(chat_id, message_id):
    try:
        bot.delete_message(chat_id, message_id)
    except:
        pass

def add_to_delete(chat_id, message_id):
    if chat_id not in last_messages:
        last_messages[chat_id] = []
    last_messages[chat_id].append(message_id)
    if len(last_messages[chat_id]) > 10:
        old_id = last_messages[chat_id].pop(0)
        try:
            bot.delete_message(chat_id, old_id)
        except:
            pass

def delete_previous_messages(chat_id, user_id):
    if chat_id in last_messages:
        for msg_id in last_messages[chat_id][:]:
            try:
                bot.delete_message(chat_id, msg_id)
            except:
                pass
        last_messages[chat_id] = []

def clean_before_action(chat_id, user_id, user_message_id):
    delete_message(chat_id, user_message_id)
    if chat_id in last_messages and last_messages[chat_id]:
        for msg_id in last_messages[chat_id][-3:]:
            delete_message(chat_id, msg_id)

# ================= ОБРАБОТЧИК ВСТУПЛЕНИЯ В КАНАЛ ПО ССЫЛКЕ =================

@bot.chat_member_handler()
def handle_chat_member(chat_member_update):
    try:
        invite_link = chat_member_update.invite_link
        if not invite_link:
            return
        
        link_name = invite_link.name
        if not link_name or not link_name.startswith("giveaway_"):
            return
        
        giveaway_id = link_name.replace("giveaway_", "")
        user_id = chat_member_update.from_user.id
        user_name = chat_member_update.from_user.username or chat_member_update.from_user.first_name
        
        print(f"🔔 Пользователь {user_name} ({user_id}) вступил по ссылке розыгрыша {giveaway_id}")
        
        if giveaway_id not in db["giveaways"]:
            return
        
        giveaway = db["giveaways"][giveaway_id]
        
        if not giveaway.get("is_active"):
            return
        
        if user_id in giveaway["participants"]:
            print(f"ℹ️ Пользователь {user_name} уже участвует")
            return
        
        giveaway["participants"].append(user_id)
        save_db(db)
        
        try:
            update_participation_button(giveaway)
        except Exception as e:
            print(f"Ошибка обновления кнопки: {e}")
        
        update_user_stats(giveaway["creator_id"], participants=1)
        
        try:
            bot.send_message(
                user_id,
                "🎉 <b>Вы стали участником конкурса!</b>\n\nЖелаем удачи! 🍀",
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"Не удалось отправить сообщение: {e}")
        
        if giveaway.get("target_participants") and len(giveaway["participants"]) >= giveaway["target_participants"]:
            conclude_giveaway(giveaway_id)
        
        print(f"✅ Пользователь {user_name} добавлен в розыгрыш {giveaway_id}")
        
    except Exception as e:
        print(f"ОШИБКА В handle_chat_member: {e}")
        traceback.print_exc()

@bot.my_chat_member_handler()
def handle_bot_chat_member_update(update):
    """Обрабатывает изменения статуса бота в каналах (добавление и удаление)"""
    try:
        new_chat_member = update.new_chat_member
        old_chat_member = update.old_chat_member
        chat = update.chat
        
        # Проверяем, что это канал
        if chat.type not in ['channel', 'supergroup']:
            return
        
        # Определяем пользователя, который вызвал изменение
        user_id = None
        if update.invite_link and update.invite_link.inviter:
            user_id = update.invite_link.inviter.id
        else:
            user_id = update.from_user.id
        
        channel_id = str(chat.id)
        channel_name = chat.title if chat.title else channel_id
        
        # ===== СЛУЧАЙ 1: БОТА ДОБАВИЛИ КАК АДМИНИСТРАТОРА =====
        if (new_chat_member.status == 'administrator' and 
            old_chat_member.status != 'administrator'):
            
            print(f"🔔 Бот добавлен в канал {channel_name} пользователем {user_id}")
            
            if add_user_channel(user_id, channel_id):
                bot.send_message(
                    user_id,
                    f"<blockquote><b>Канал {channel_name} успешно подключен!</b></blockquote>\n\n",
                    parse_mode="HTML"
                )
            else:
                bot.send_message(
                    user_id,
                    f"<b>Канал {channel_name} уже был в вашем списке.</b>",
                    parse_mode="HTML"
                )
        
        # ===== СЛУЧАЙ 2: БОТА УБРАЛИ ИЗ АДМИНИСТРАТОРОВ =====
        elif (old_chat_member.status == 'administrator' and 
              new_chat_member.status != 'administrator'):
            
            print(f"🔴 Бот удалён из канала {channel_name}")
            
            # Удаляем канал у всех пользователей
            removed_users = []
            for uid_str, channels in db.get("user_channels", {}).items():
                if channel_id in channels:
                    channels.remove(channel_id)
                    removed_users.append(uid_str)
            
            if removed_users:
                save_db(db)
                print(f"🔴 Канал {channel_name} удалён у {len(removed_users)} пользователей")
                
                for uid_str in removed_users:
                    try:
                        bot.send_message(
                            int(uid_str),
                            f"<b>Канал {channel_name} был автоматически удалён из вашего списка!</b>\n\n"
                            f"<blockquote><i>Бот больше не является администратором этого канала.</i></blockquote>",
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        print(f"Не удалось уведомить {uid_str}: {e}")
                        
    except Exception as e:
        print(f"Ошибка в handle_bot_chat_member_update: {e}")
        traceback.print_exc()

# ================= ВСПОМОГАТЕЛЬНЫЕ =================

def get_user(user_id: int) -> dict:
    uid = str(user_id)
    if uid not in db["users"]:
        db["users"][uid] = {
            "giveaways_created": 0,
            "total_participants": 0
        }
        save_db(db)
    if "total_participants" not in db["users"][uid]:
        db["users"][uid]["total_participants"] = 0
        save_db(db)
    if "giveaways_created" not in db["users"][uid]:
        db["users"][uid]["giveaways_created"] = 0
        save_db(db)
    return db["users"][uid]

def add_user_channel(user_id, channel):
    uid = str(user_id)
    if "user_channels" not in db:
        db["user_channels"] = {}
    if uid not in db["user_channels"]:
        db["user_channels"][uid] = []
    if channel not in db["user_channels"][uid]:
        db["user_channels"][uid].append(channel)
        save_db(db)
        return True
    return False

def get_user_channels(user_id):
    uid = str(user_id)
    if "user_channels" not in db:
        db["user_channels"] = {}
    return db["user_channels"].get(uid, [])

def get_channel_display_name(channel_identifier):
    try:
        if str(channel_identifier).startswith('-100') or str(channel_identifier).lstrip('-').isdigit():
            chat = bot.get_chat(int(channel_identifier))
            return chat.title if chat.title else channel_identifier
        else:
            chat = bot.get_chat(channel_identifier)
            return chat.title if chat.title else channel_identifier
    except:
        pass
    return channel_identifier

def generate_giveaway_id() -> str:
    return ''.join(random.choices(string.ascii_letters + string.digits, k=12))

def generate_postlot_key(giveaway_id):
    secret = "FastRandom_Secret_2026"
    raw = f"{giveaway_id}_{secret}"
    return hashlib.md5(raw.encode()).hexdigest()[:32]

def get_display_name(user_id: int) -> str:
    try:
        user = bot.get_chat(user_id)
        return f"@{user.username}" if user.username else user.first_name
    except:
        return str(user_id)

def update_user_stats(user_id: int, created: bool = False, participants: int = 0):
    user = get_user(user_id)
    if created:
        user["giveaways_created"] = user.get("giveaways_created", 0) + 1
    if participants > 0:
        user["total_participants"] = user.get("total_participants", 0) + participants
    save_db(db)

def update_participation_button(giveaway_data: dict):
    button_text = giveaway_data.get("button_text", "Участвовать")
    button_color = giveaway_data.get("button_color", "default")
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    giveaway_id = giveaway_data.get('giveaway_id')
    bot_link = f"https://t.me/{BOT_USERNAME}?start=giveaway_{giveaway_id}"
    markup.add(create_colored_button(button_text, url=bot_link, color=button_color))
    try:
        bot.edit_message_reply_markup(giveaway_data["chat_id"], giveaway_data["message_id"], reply_markup=markup)
    except Exception as e:
        print(f"Ошибка обновления кнопки: {e}")

def conclude_giveaway(giveaway_id):
    if giveaway_id not in db["giveaways"]:
        return
    
    giveaway = db["giveaways"][giveaway_id]
    
    if not giveaway["is_active"]:
        return
    
    giveaway["is_active"] = False
    participants = giveaway["participants"]
    winners_count = min(giveaway["winners_count"], len(participants))
    
    if len(participants) == 0:
        results_text = "Результаты конкурса:\n<blockquote>Победителей нет</blockquote>"
    else:
        winners = random.sample(participants, winners_count)
        winners_text = ""
        for i, winner in enumerate(winners, 1):
            winners_text += f"{i}. {get_display_name(winner)}\n"
        results_text = f"Результаты конкурса:\n<b>Победители:</b>\n<blockquote>{winners_text}</blockquote>"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(create_colored_button("Завершён", f"results_{giveaway_id}", color="danger"))
    
    try:
        bot.edit_message_reply_markup(
            giveaway["chat_id"],
            giveaway["message_id"],
            reply_markup=markup
        )
        
        if "message_id" in giveaway:
            bot.send_message(
                giveaway["chat_id"],
                results_text,
                parse_mode="HTML",
                reply_to_message_id=giveaway["message_id"]
            )
        else:
            bot.send_message(
                giveaway["chat_id"],
                results_text,
                parse_mode="HTML"
            )
    except Exception as e:
        print(f"Ошибка при завершении: {e}")
    
    save_db(db)

# ================= ГЛАВНОЕ МЕНЮ =================

def show_main_keyboard(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        create_colored_button("Создать розыгрыш", "main_create_giveaway", color="primary"),
        create_colored_button("Мои розыгрыши", "main_my_giveaways", color="success"),
        create_colored_button("Мои каналы", "main_my_channels", color="default")
    )
    bot.send_message(chat_id, "👇 Выберите действие", reply_markup=markup)

def main_menu(chat_id, user_id):
    if chat_id in last_messages:
        for msg_id in last_messages[chat_id]:
            delete_message(chat_id, msg_id)
        last_messages[chat_id] = []
    
    text = (
        "👋 <b>Добро пожаловать!</b>\n\n"
        "<blockquote><i>Наш бот поможет Вам провести розыгрыш в канале или чате</i></blockquote>"
    )
    
    # Сначала отправляем приветственное сообщение
    sent = bot.send_message(chat_id, text, parse_mode="HTML")
    add_to_delete(chat_id, sent.message_id)
    
    # Затем отправляем клавиатуру с кнопками
    show_main_keyboard(chat_id)
    
@bot.message_handler(commands=['start'])
def cmd_start(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    get_user(user_id)
    delete_message(chat_id, message.message_id)
    
    if message.text.startswith('/start giveaway_'):
        giveaway_id = message.text.replace('/start giveaway_', '')
        print(f"DEBUG: cmd_start - розыгрыш {giveaway_id}")
        
        # ЗАКОММЕНТИРОВАНО - НЕ ОЧИЩАЕМ temp_data
        # if user_id in temp_data:
        #     clear_temp(user_id)
        
        if giveaway_id in db.get("giveaways", {}):
            process_join_giveaway(user_id, giveaway_id, chat_id)
        else:
            bot.send_message(chat_id, "❌ Розыгрыш не найден.")
            main_menu(chat_id, user_id)
    else:
        main_menu(chat_id, user_id)

def process_join_giveaway(user_id, giveaway_id, chat_id):
    
    get_user(user_id)
    
    if giveaway_id not in db.get("giveaways", {}):
        bot.send_message(chat_id, "❌ <b>Розыгрыш не найден!</b>", parse_mode="HTML")
        main_menu(chat_id, user_id)
        return
    
    giveaway = db["giveaways"][giveaway_id]
    
    if not giveaway.get("is_active"):
        bot.send_message(chat_id, "⏰ <b>Розыгрыш уже завершён!</b>", parse_mode="HTML")
        main_menu(chat_id, user_id)
        return
    
    if user_id in giveaway.get("participants", []):
        bot.send_message(chat_id, "✅ <b>Вы уже участвуете в розыгрыше!</b>", parse_mode="HTML")
        main_menu(chat_id, user_id)
        return
    
    required_channel_ids = giveaway.get("required_channel_ids", [])
    required_invite_links = giveaway.get("required_invite_links", [])
    
    not_subscribed = []
    not_subscribed_links = []
    need_check_button = False
    
    for i, channel_id_str in enumerate(required_channel_ids):
        try:
            channel_id = int(channel_id_str)
            member = bot.get_chat_member(channel_id, user_id)
            if member.status in ['left', 'kicked']:
                not_subscribed.append(channel_id_str)
                link = required_invite_links[i] if i < len(required_invite_links) else None
                not_subscribed_links.append(link)
                try:
                    chat = bot.get_chat(channel_id)
                    if chat.username:
                        need_check_button = True
                except:
                    pass
        except:
            not_subscribed.append(channel_id_str)
            not_subscribed_links.append(required_invite_links[i] if i < len(required_invite_links) else None)
    
    if not not_subscribed:
        giveaway["participants"].append(user_id)
        update_participation_button(giveaway)
        save_db(db)
        
        if giveaway.get("target_participants") and len(giveaway["participants"]) >= giveaway["target_participants"]:
            conclude_giveaway(giveaway_id)
        
        bot.send_message(chat_id, "🎉 <b>Вы участвуете в розыгрыше!</b>\n\nЖелаем удачи! 🍀", parse_mode="HTML")
        main_menu(chat_id, user_id)
    else:
        text = "<blockquote>😡 <b>Вы не выполнили условия конкурса‼️</b></blockquote>\n\n"
        markup = types.InlineKeyboardMarkup(row_width=1)
        
        for i, link in enumerate(not_subscribed_links):
            if link:
                markup.add(types.InlineKeyboardButton(f"Подписаться", url=link))
                text += f"<b>Подпишитесь на канал</b>\n"
        
        if need_check_button:
            markup.add(types.InlineKeyboardButton("Я подписался", callback_data=f"check_{giveaway_id}"))
        
        markup.add(types.InlineKeyboardButton("В меню", callback_data="back_to_main_menu"))
        
        bot.send_message(
            chat_id, 
            text + "\n<b>После выполнения условий Вы станете участником конкурса!</b>",
            parse_mode="HTML",
            reply_markup=markup
        )

@bot.message_handler(commands=['create'])
def cmd_create(message):
    user_id = message.from_user.id
    get_user(user_id)
    delete_message(message.chat.id, message.message_id)
    
    if user_id in temp_data:
        clear_temp(user_id)
    
    main_menu(message.chat.id, user_id)
    
    channels = get_user_channels(user_id)
    
    show_main_keyboard(message.chat.id)
    
    text = "🧰 <b>Создание розыгрыша</b>\n\n<blockquote>Выберите канал, где будет опубликован пост розыгрыша</blockquote>"
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    for channel in channels:
        channel_title = channel
        try:
            chat = bot.get_chat(channel)
            if chat.title:
                channel_title = chat.title
        except:
            pass
        markup.add(types.InlineKeyboardButton(f"{channel_title}", callback_data=f"select_channel_{channel}_{user_id}"))
    
    markup.add(types.InlineKeyboardButton("➕ Добавить канал", callback_data="add_channel_step"))
    
    sent = bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=markup)
    add_to_delete(message.chat.id, sent.message_id)

# ================= МОИ РОЗЫГРЫШИ =================

@bot.message_handler(func=lambda msg: msg.text == "Мои розыгрыши")
def menu_my_giveaways(msg):
    clean_before_action(msg.chat.id, msg.from_user.id, msg.message_id)
    user_id = msg.from_user.id
    my_giveaways = []
    for g_id, g in db["giveaways"].items():
        if g.get("creator_id") == user_id:
            g["giveaway_id"] = g_id
            my_giveaways.append(g)
    
    show_main_keyboard(msg.chat.id)
    
    if not my_giveaways:
        sent = bot.send_message(msg.chat.id, "<blockquote>📭 <b>У вас пока нет созданных розыгрышей.</b></blockquote>", parse_mode="HTML")
        add_to_delete(msg.chat.id, sent.message_id)
        return
    
    my_giveaways.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    
    text = "<b>💬 Мои розыгрыши</b>\n\n<i>Выберите розыгрыш:</i>"
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    for g in my_giveaways[:10]:
        giveaway_id = g.get("giveaway_id", "unknown")
        short_id = giveaway_id[-4:] if len(giveaway_id) >= 4 else giveaway_id
        created_at = g.get("created_at")
        if created_at:
            if isinstance(created_at, str):
                try:
                    dt = datetime.datetime.fromisoformat(created_at)
                    created_str = dt.strftime("%d.%m %H:%M")
                except:
                    created_str = "дата неизвестна"
            else:
                created_str = "дата неизвестна"
        else:
            created_str = "дата неизвестна"
        
        status_emoji = "🟢" if g.get("is_active") else "🔴"
        button_text = f"{status_emoji} Розыгрыш #{short_id} | {created_str}"
        markup.add(types.InlineKeyboardButton(button_text, callback_data=f"view_giveaway_{giveaway_id}_{user_id}"))
    
    sent = bot.send_message(msg.chat.id, text, parse_mode="HTML", reply_markup=markup)
    add_to_delete(msg.chat.id, sent.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("view_giveaway_"))
def view_giveaway_detail(call):
    parts = call.data.split("_")
    giveaway_id = parts[2]
    user_id = int(parts[3])
    
    if call.from_user.id != user_id:
        bot.answer_callback_query(call.id, "❌ Не ваш розыгрыш", show_alert=True)
        return
    
    if giveaway_id not in db["giveaways"]:
        bot.answer_callback_query(call.id, "❌ Розыгрыш не найден", show_alert=True)
        return
    
    g = db["giveaways"][giveaway_id]
    
    short_id = giveaway_id[-4:] if len(giveaway_id) >= 4 else giveaway_id
    status = "🟢 Активен" if g.get("is_active") else "🔴 Завершён"
    
    created_at = g.get("created_at")
    if created_at:
        if isinstance(created_at, str):
            try:
                dt = datetime.datetime.fromisoformat(created_at)
                created_str = dt.strftime("%d.%m.%Y %H:%M")
            except:
                created_str = "дата неизвестна"
        else:
            created_str = "дата неизвестна"
    else:
        created_str = "дата неизвестна"
    
    publish_type = "список победителей" if not g.get("is_active") else "кнопка участия"
    
    # Проверяем, можно ли добавить ещё победителей
    participants = g.get("participants", [])
    selected_winners = g.get("selected_winners", [])
    can_add_more = len(selected_winners) < len(participants)
    
    text = f"<b>🎉 Розыгрыш #{short_id}</b>\n\n"
    text += f"<i>Статус:</i> <b>{status}</b>\n"
    text += f"<i>Создан:</i> <b>{created_str}</b>\n"
    text += f"<i>Победителей:</i> <b>{g.get('winners_count', 1)}</b>\n"
    text += f"<i>Участников:</i> <b>{len(participants)}</b>\n\n"
    text += f"<blockquote>Тип публикации в канале после завершения: <b>{publish_type}</b></blockquote>"
    
    # Показываем уже выбранных победителей (если есть)
    if g.get("selected_winners"):
        text += "\n\n<blockquote><b>Выбранные победители:</b></blockquote>\n"
        for i, w in enumerate(g.get("selected_winners", []), 1):
            text += f"{i}. {get_display_name(w)}\n"
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    if g.get("is_active"):
        markup.add(
            types.InlineKeyboardButton("🎲 Подвести итоги", callback_data=f"end_now_{giveaway_id}_{user_id}"),
            types.InlineKeyboardButton("➕ Доп. победители", callback_data=f"add_winners_{giveaway_id}_{user_id}"),
            types.InlineKeyboardButton("🗑 Удалить", callback_data=f"del_giveaway_{giveaway_id}_{user_id}")
        )
    else:
        # Даже после завершения показываем кнопку добавления победителей, если есть кого добавить
        if can_add_more:
            markup.add(
                types.InlineKeyboardButton("➕ Добавить победителей", callback_data=f"add_winners_{giveaway_id}_{user_id}"),
                types.InlineKeyboardButton("🗑 Удалить", callback_data=f"del_giveaway_{giveaway_id}_{user_id}")
            )
        else:
            markup.add(types.InlineKeyboardButton("🗑 Удалить", callback_data=f"del_giveaway_{giveaway_id}_{user_id}"))
    
    markup.add(types.InlineKeyboardButton("‹ Назад", callback_data=f"back_to_list_{user_id}"))
    
    try:
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML",
            reply_markup=markup,
            disable_web_page_preview=True
        )
    except:
        pass
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("back_to_list_"))
def back_to_list(call):
    user_id = int(call.data.split("_")[3])
    
    if call.from_user.id != user_id:
        bot.answer_callback_query(call.id, "❌ Не ваш список", show_alert=True)
        return
    
    # Просто показываем список розыгрышей
    my_giveaways = []
    for g_id, g in db["giveaways"].items():
        if g.get("creator_id") == user_id:
            g["giveaway_id"] = g_id
            my_giveaways.append(g)
    
    if not my_giveaways:
        bot.edit_message_text(
            "<blockquote>📭 <b>У вас пока нет созданных розыгрышей.</b></blockquote>",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML"
        )
        return
    
    my_giveaways.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    
    text = "<b>💬 Мои розыгрыши</b>\n\n<i>Выберите розыгрыш:</i>"
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    for g in my_giveaways[:10]:
        giveaway_id = g.get("giveaway_id", "unknown")
        short_id = giveaway_id[-4:] if len(giveaway_id) >= 4 else giveaway_id
        created_at = g.get("created_at")
        if created_at:
            if isinstance(created_at, str):
                try:
                    dt = datetime.datetime.fromisoformat(created_at)
                    created_str = dt.strftime("%d.%m %H:%M")
                except:
                    created_str = "дата неизвестна"
            else:
                created_str = "дата неизвестна"
        else:
            created_str = "дата неизвестна"
        
        status_emoji = "🟢" if g.get("is_active") else "🔴"
        button_text = f"{status_emoji} Розыгрыш #{short_id} | {created_str}"
        markup.add(types.InlineKeyboardButton(button_text, callback_data=f"view_giveaway_{giveaway_id}_{user_id}"))
    
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="HTML", reply_markup=markup)
    except:
        pass
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("end_now_"))
def end_now(call):
    parts = call.data.split("_")
    if len(parts) < 3:
        bot.answer_callback_query(call.id, "❌ Розыгрыш завершен", show_alert=True)
        return
    
    giveaway_id = parts[2]
    user_id = int(parts[3]) if len(parts) > 3 else call.from_user.id
    
    if call.from_user.id != user_id:
        bot.answer_callback_query(call.id, "❌ Не ваш розыгрыш", show_alert=True)
        return
    
    if giveaway_id not in db["giveaways"]:
        bot.answer_callback_query(call.id, "❌ Розыгрыш не найден", show_alert=True)
        return
    
    bot.answer_callback_query(call.id, "✅ Подводим итоги...")
    conclude_giveaway(giveaway_id)
    view_giveaway_detail(call)
@bot.callback_query_handler(func=lambda call: call.data.startswith("add_winners_"))
def add_winners_callback(call):
    parts = call.data.split("_")
    if len(parts) < 3:
        bot.answer_callback_query(call.id, "❌ Ошибка", show_alert=True)
        return
    
    giveaway_id = parts[2]
    user_id = int(parts[3]) if len(parts) > 3 else call.from_user.id
    
    if call.from_user.id != user_id:
        bot.answer_callback_query(call.id, "❌ Не ваш розыгрыш", show_alert=True)
        return
    
    giveaway = db["giveaways"].get(giveaway_id)
    if giveaway is None:
        bot.answer_callback_query(call.id, "❌ Розыгрыш не найден", show_alert=True)
        return
    
    participants = giveaway.get("participants", [])
    already_selected = giveaway.get("selected_winners", [])
    current_winners = len(already_selected)
    max_winners = len(participants)
    
    if current_winners >= max_winners:
        bot.answer_callback_query(call.id, f"❌ Уже выбраны все {max_winners} участников!", show_alert=True)
        return
    
    # Показываем уже выбранных победителей
    winners_text = ""
    if already_selected:
        winners_text = "\n\n<b>Уже выбраны и опубликованы в канале:</b>\n"
        for i, w in enumerate(already_selected, 1):
            winners_text += f"{i}. {get_display_name(w)}\n"
    
    msg = bot.send_message(
        call.message.chat.id,
        f"<b>Всего участников: {max_winners}</b>\n\n"
        f"<b>Сколько победителей добавить?</b>\n"
        f"<i>Победители будут опубликованы в канале!</i>{winners_text}",
        parse_mode="HTML"
    )
    add_to_delete(call.message.chat.id, msg.message_id)
    
    bot.register_next_step_handler(msg, process_add_winners_count, giveaway_id, user_id, current_winners, max_winners, call.message.chat.id)


def process_add_winners_count(message, giveaway_id, user_id, current_winners, max_winners, chat_id):
    delete_message(chat_id, message.message_id)
    
    try:
        additional = int(message.text.strip())
        if additional < 1:
            raise ValueError
        new_count = current_winners + additional
        if new_count > max_winners:
            raise ValueError
    except:
        sent = bot.send_message(chat_id, f"❌ <b>Введите число от 1 до {max_winners - current_winners}</b>", parse_mode="HTML")
        add_to_delete(chat_id, sent.message_id)
        main_menu(chat_id, user_id)
        return
    
    giveaway = db["giveaways"].get(giveaway_id)
    if giveaway is None:
        bot.send_message(chat_id, "❌ Розыгрыш не найден", parse_mode="HTML")
        main_menu(chat_id, user_id)
        return
    
    participants = giveaway.get("participants", [])
    already_selected = giveaway.get("selected_winners", [])
    
    # Выбираем дополнительных победителей из тех, кто ещё не выиграл
    available = [p for p in participants if p not in already_selected]
    
    if len(available) < additional:
        bot.send_message(chat_id, f"❌ Недостаточно участников для выбора {additional} победителей!", parse_mode="HTML")
        main_menu(chat_id, user_id)
        return
    
    new_winners = random.sample(available, additional)
    
    # Сохраняем всех победителей
    if "selected_winners" not in giveaway:
        giveaway["selected_winners"] = []
    giveaway["selected_winners"].extend(new_winners)
    giveaway["winners_count"] = len(giveaway["selected_winners"])
    db["giveaways"][giveaway_id] = giveaway
    save_db(db)
    
    # Формируем сообщение с победителями
    winners_text = ""
    for i, winner in enumerate(giveaway["selected_winners"], 1):
        winners_text += f"{i}. {get_display_name(winner)}\n"
    
    # ========== ОТПРАВЛЯЕМ В КАНАЛ ==========
    channel_id = giveaway.get("channel")
    message_id = giveaway.get("message_id")
    
    # Текст для публикации в канале
    channel_message = (
        f"<b>Дополнительные победители!</b>\n\n"
        f"<b>Победители:</b>\n<blockquote>{winners_text}</blockquote>"
    )
    
    try:
        # Отправляем в канал ответом на сообщение с розыгрышем
        if message_id:
            bot.send_message(
                channel_id,
                channel_message,
                parse_mode="HTML",
                reply_to_message_id=message_id
            )
        else:
            bot.send_message(
                channel_id,
                channel_message,
                parse_mode="HTML"
            )
        
        # Также отправляем уведомление создателю в бота
        bot.send_message(
            user_id,
            f"✅ <b>Дополнительные победители добавлены и опубликованы в канале!</b>\n\n{winners_text}",
            parse_mode="HTML"
        )
        
    except Exception as e:
        # Если не удалось отправить в канал, отправляем ошибку создателю
        bot.send_message(
            user_id,
            f"❌ <b>Не удалось опубликовать победителей в канале!</b>\n\nОшибка: {str(e)[:100]}\n\nПобедители:\n{winners_text}",
            parse_mode="HTML"
        )
    
    # Отправляем новое сообщение с обновлённой статистикой
    g = giveaway
    short_id = giveaway_id[-4:] if len(giveaway_id) >= 4 else giveaway_id
    status = "🟢 Активен" if g.get("is_active") else "🔴 Завершён"
    
    created_at = g.get("created_at")
    if created_at:
        if isinstance(created_at, str):
            try:
                dt = datetime.datetime.fromisoformat(created_at)
                created_str = dt.strftime("%d.%m.%Y %H:%M")
            except:
                created_str = "дата неизвестна"
        else:
            created_str = "дата неизвестна"
    else:
        created_str = "дата неизвестна"
    
    publish_type = "список победителей" if not g.get("is_active") else "кнопка участия"
    participants_list = g.get("participants", [])
    selected_winners_list = g.get("selected_winners", [])
    can_add_more = len(selected_winners_list) < len(participants_list)
    
    text = f"<b>🎁 Розыгрыш #{short_id}</b>\n\n"
    text += f"Статус: {status}\n"
    text += f"Создан: {created_str}\n"
    text += f"Победителей: {g.get('winners_count', 1)}\n"
    text += f"Участников: {len(participants_list)}\n\n"
    text += f"Тип публикации в канале после завершения: {publish_type}"
    
    if g.get("selected_winners"):
        text += "\n\n<b>🏆 Выбранные победители:</b>\n"
        for i, w in enumerate(g.get("selected_winners", []), 1):
            text += f"{i}. {get_display_name(w)}\n"
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    if g.get("is_active"):
        markup.add(
            types.InlineKeyboardButton("🎲 Подвести итоги", callback_data=f"end_now_{giveaway_id}_{user_id}"),
            types.InlineKeyboardButton("➕ Доп. победители", callback_data=f"add_winners_{giveaway_id}_{user_id}"),
            types.InlineKeyboardButton("🗑 Удалить", callback_data=f"del_giveaway_{giveaway_id}_{user_id}")
        )
    else:
        if can_add_more:
            markup.add(
                types.InlineKeyboardButton("➕ Добавить победителей", callback_data=f"add_winners_{giveaway_id}_{user_id}"),
                types.InlineKeyboardButton("🗑 Удалить", callback_data=f"del_giveaway_{giveaway_id}_{user_id}")
            )
        else:
            markup.add(types.InlineKeyboardButton("🗑 Удалить", callback_data=f"del_giveaway_{giveaway_id}_{user_id}"))
    
    markup.add(types.InlineKeyboardButton("‹ Назад", callback_data=f"back_to_list_{user_id}"))
    
    bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)
    
    main_menu(chat_id, user_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("results_"))
def results_callback(call):
    parts = call.data.split("_")
    if len(parts) < 3:
        bot.answer_callback_query(call.id, "❌ Розыгрыш завершен", show_alert=True)
        return
    
    giveaway_id = parts[2]
    user_id = int(parts[3]) if len(parts) > 3 else call.from_user.id
    
    if call.from_user.id != user_id:
        bot.answer_callback_query(call.id, "❌ Не ваш розыгрыш", show_alert=True)
        return
    
    if giveaway_id not in db["giveaways"]:
        bot.answer_callback_query(call.id, "❌ Розыгрыш не найден", show_alert=True)
        return
    
    giveaway = db["giveaways"][giveaway_id]
    
    if giveaway["is_active"]:
        bot.answer_callback_query(call.id, "⏰ Розыгрыш ещё не завершён!", show_alert=True)
        return
    
    text = (
        "📊 <b>Статистика розыгрыша</b>\n\n"
        f"👥 Участников: {len(giveaway['participants'])}\n"
        f"🏆 Победителей: {min(giveaway['winners_count'], len(giveaway['participants']))}\n\n"
        f"Результаты объявлены в канале."
    )
    
    bot.answer_callback_query(call.id, text, show_alert=True, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith("del_giveaway_"))
def delete_giveaway_confirm(call):
    parts = call.data.split("_")
    if len(parts) < 3:
        bot.answer_callback_query(call.id, "❌ Розыгрыш завершен", show_alert=True)
        return
    
    giveaway_id = parts[2]
    user_id = int(parts[3]) if len(parts) > 3 else call.from_user.id
    
    if call.from_user.id != user_id:
        bot.answer_callback_query(call.id, "❌ Не ваш розыгрыш", show_alert=True)
        return
    
    if giveaway_id not in db["giveaways"]:
        bot.answer_callback_query(call.id, "❌ Розыгрыш не найден", show_alert=True)
        return
    
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ Да, удалить", callback_data=f"confirm_del_{giveaway_id}_{user_id}"),
        types.InlineKeyboardButton("❌ Отмена", callback_data=f"view_giveaway_{giveaway_id}_{user_id}")
    )
    
    bot.edit_message_text(
        "<b>Удалить этот розыгрыш?</b>\n\nЭто действие нельзя отменить.",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="HTML",
        reply_markup=markup
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_del_"))
def confirm_delete_giveaway(call):
    parts = call.data.split("_")
    if len(parts) < 3:
        bot.answer_callback_query(call.id, "❌ Розыгрыш завершен", show_alert=True)
        return
    
    giveaway_id = parts[2]
    user_id = int(parts[3]) if len(parts) > 3 else call.from_user.id
    
    if call.from_user.id != user_id:
        bot.answer_callback_query(call.id, "❌ Не ваш розыгрыш", show_alert=True)
        return
    
    if giveaway_id not in db["giveaways"]:
        bot.answer_callback_query(call.id, "❌ Розыгрыш не найден", show_alert=True)
        return
    
    del db["giveaways"][giveaway_id]
    save_db(db)
    
    bot.answer_callback_query(call.id, "✅ Розыгрыш удалён")
    menu_my_giveaways(call.message)

# ================= МОИ КАНАЛЫ =================

@bot.message_handler(func=lambda msg: msg.text == "Мои каналы")
def menu_my_channels(msg):
    clean_before_action(msg.chat.id, msg.from_user.id, msg.message_id)
    user_id = msg.from_user.id
    channels = get_user_channels(user_id)
    
    show_main_keyboard(msg.chat.id)
    
    text = "<b>Мои каналы</b>\n\n"
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    for channel in channels:
        display_name = get_channel_display_name(channel)
        markup.add(types.InlineKeyboardButton(f"{display_name}", callback_data=f"chan_mng_{channel}_{user_id}"))
    
    markup.add(types.InlineKeyboardButton("➕ Добавить канал", callback_data="chan_add_new"))
    markup.add(types.InlineKeyboardButton("‹ Назад", callback_data="back_to_main_menu"))
    
    if not channels:
        text += "<blockquote>У вас пока нет добавленных каналов.</blockquote>\n\n"
    else:
        text += "<blockquote><b>Выберите канал или добавьте новый:</b></blockquote>"
    
    sent = bot.send_message(msg.chat.id, text, parse_mode="HTML", reply_markup=markup)
    add_to_delete(msg.chat.id, sent.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("chan_mng_"))
def manage_channel(call):
    parts = call.data.split("_")
    channel = parts[2]
    user_id = int(parts[3])
    
    if call.from_user.id != user_id:
        bot.answer_callback_query(call.id, "❌ Это не ваш канал", show_alert=True)
        return
    
    display_name = get_channel_display_name(channel)
    
    text = f"<b>Выбранный канал {display_name}</b>\n\n<blockquote>Выберите действие:</blockquote>"
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🗑 Удалить канал", callback_data=f"chan_del_{channel}_{user_id}"),
        types.InlineKeyboardButton("◀️ Назад", callback_data="back_to_channels_list")
    )
    
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="HTML", reply_markup=markup)
    except:
        bot.send_message(call.message.chat.id, text, parse_mode="HTML", reply_markup=markup)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("chan_del_"))
def delete_channel(call):
    parts = call.data.split("_")
    channel = parts[2]
    user_id = int(parts[3])
    
    if call.from_user.id != user_id:
        bot.answer_callback_query(call.id, "❌ Это не ваш канал", show_alert=True)
        return
    
    uid = str(user_id)
    if channel in db["user_channels"].get(uid, []):
        db["user_channels"][uid].remove(channel)
        save_db(db)
        bot.answer_callback_query(call.id, f"✅ Канал {channel} удалён")
    else:
        bot.answer_callback_query(call.id, "❌ Канал не найден", show_alert=True)
        return
    
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass
    
    menu_my_channels(call.message)

@bot.callback_query_handler(func=lambda call: call.data == "back_to_channels_list")
def back_to_channels_list(call):
    menu_my_channels(call.message)

@bot.callback_query_handler(func=lambda call: call.data == "chan_add_new")
def add_channel_from_menu(call):
    clean_before_action(call.message.chat.id, call.from_user.id, call.message.message_id)
    bot.answer_callback_query(call.id)
    delete_previous_messages(call.message.chat.id, None)
    text = "📢 <b>Введите @username канала</b>\n\n<blockquote>⚠️ Бот должен быть администратором канала!\n\nОтправьте @username канала:</blockquote>"
    msg = bot.send_message(call.message.chat.id, text, parse_mode="HTML")
    add_to_delete(call.message.chat.id, msg.message_id)
    bot.register_next_step_handler(msg, process_channel_from_menu)

def process_channel_from_menu(message):
    user_id = message.from_user.id
    delete_message(message.chat.id, message.message_id)
    
    channel_id = None
    display_name = None
    
    if message.forward_from_chat:
        chat = message.forward_from_chat
        channel_id = str(chat.id)
        display_name = chat.title if chat.title else str(chat.id)
        try:
            bot.get_chat_member(chat.id, bot.get_me().id)
        except:
            sent = bot.send_message(
                message.chat.id, 
                "❌ <b>Бот не является администратором этого канала!</b>\n\n"
                "Добавьте бота в администраторы и попробуйте снова.",
                parse_mode="HTML"
            )
            add_to_delete(message.chat.id, sent.message_id)
            menu_my_channels(message)
            return
    
    elif message.text and message.text.strip().startswith('@'):
        channel_input = message.text.strip()
        try:
            chat = bot.get_chat(channel_input)
            bot.get_chat_member(chat.id, bot.get_me().id)
            channel_id = str(chat.id)
            display_name = chat.title if chat.title else channel_input
        except:
            sent = bot.send_message(
                message.chat.id, 
                f"❌ <b>Канал {channel_input} не найден или бот не администратор!</b>",
                parse_mode="HTML"
            )
            add_to_delete(message.chat.id, sent.message_id)
            menu_my_channels(message)
            return
    
    else:
        sent = bot.send_message(
            message.chat.id, 
            "<b>Чтобы добавить канал:</b>\n\n"
            "<blockquote><i>1. Перешлите ЛЮБОЕ сообщение из канала\n"
            "2. Или отправьте @username публичного канала</i></blockquote>",
            parse_mode="HTML"
        )
        add_to_delete(message.chat.id, sent.message_id)
        menu_my_channels(message)
        return
    
    if channel_id:
        user_channels = get_user_channels(user_id)
        if channel_id in user_channels:
            sent = bot.send_message(message.chat.id, f"⚠️ <b>Канал {display_name} уже добавлен!</b>", parse_mode="HTML")
            add_to_delete(message.chat.id, sent.message_id)
            menu_my_channels(message)
            return
        
        add_user_channel(user_id, channel_id)
        sent = bot.send_message(message.chat.id, f"✅ <b>Канал {display_name} успешно добавлен!</b>", parse_mode="HTML")
        add_to_delete(message.chat.id, sent.message_id)
    else:
        sent = bot.send_message(message.chat.id, "❌ <b>Не удалось определить канал</b>", parse_mode="HTML")
        add_to_delete(message.chat.id, sent.message_id)
    
    menu_my_channels(message)

# ================= МАСТЕР СОЗДАНИЯ =================

@bot.message_handler(func=lambda msg: msg.text == "Создать розыгрыш")
def menu_create(msg):
    clean_before_action(msg.chat.id, msg.from_user.id, msg.message_id)
    user_id = msg.from_user.id
    channels = get_user_channels(user_id)
    
    show_main_keyboard(msg.chat.id)
    
    text = "🧰 <b>Создание розыгрыша</b>\n\n<blockquote>Выберите канал, где будет опубликован пост розыгрыша</blockquote>"
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    for channel in channels:
        channel_title = channel
        try:
            chat = bot.get_chat(channel)
            if chat.title:
                channel_title = chat.title
        except:
            pass
        markup.add(types.InlineKeyboardButton(f"{channel_title}", callback_data=f"select_channel_{channel}_{user_id}"))
    
    markup.add(types.InlineKeyboardButton("➕ Добавить канал", callback_data="add_channel_step"))
    
    sent = bot.send_message(msg.chat.id, text, parse_mode="HTML", reply_markup=markup)
    add_to_delete(msg.chat.id, sent.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("select_channel_"))
def select_channel(call):
    parts = call.data.split("_")
    channel = parts[2]
    user_id = int(parts[3])
    
    if call.from_user.id != user_id:
        bot.answer_callback_query(call.id, "❌ Не Ваш выбор", show_alert=True)
        return
    
    channel_name = channel
    try:
        chat = bot.get_chat(channel)
        if chat.title:
            channel_name = chat.title
    except:
        pass
    
    temp_data[user_id] = {
        "channel": channel,
        "channel_name": channel_name,
        "step": 1,
        "description": None,
        "description_entities": None,
        "photo": None,
        "button_text": "🎁 Участвовать",
        "button_color": "default",
        "winners_count": 1,
        "end_type": None,
        "end_time": None,
        "target_participants": None,
        "required_channel_ids": [],
        "created_at": datetime.datetime.now().isoformat()
    }
    
    bot.answer_callback_query(call.id, f"✅ Выбран канал: {channel_name}")
    step_1_post(call.message.chat.id, user_id)

def clear_temp(user_id: int):
    if user_id in temp_data:
        del temp_data[user_id]

@bot.callback_query_handler(func=lambda call: call.data == "add_channel_step")
def add_channel_step(call):
    clean_before_action(call.message.chat.id, call.from_user.id, call.message.message_id)
    bot.answer_callback_query(call.id)
    delete_previous_messages(call.message.chat.id, None)
    text = "📢 <b>Введите @username канала, где будет проходить розыгрыш.</b>\n\n<blockquote>⚠️ Бот должен быть администратором канала!</blockquote>"
    msg = bot.send_message(call.message.chat.id, text, parse_mode="HTML")
    add_to_delete(call.message.chat.id, msg.message_id)
    bot.register_next_step_handler(msg, process_channel_for_giveaway)

def process_channel_for_giveaway(message):
    user_id = message.from_user.id
    delete_message(message.chat.id, message.message_id)
    channel = message.text.strip()
    if not channel.startswith('@'):
        channel = '@' + channel

    try:
        bot.get_chat_member(channel, bot.get_me().id)
    except:
        sent = bot.send_message(message.chat.id, "❌ <b>Бот не является администратором канала или канал не найден.</b>", parse_mode="HTML")
        add_to_delete(message.chat.id, sent.message_id)
        main_menu(message.chat.id, user_id)
        return

    add_user_channel(user_id, channel)
    
    sent = bot.send_message(message.chat.id, f"✅ <b>Канал {channel} успешно добавлен!</b>\n\nТеперь создайте розыгрыш.", parse_mode="HTML")
    add_to_delete(message.chat.id, sent.message_id)
    
    temp_data[user_id] = {
        "channel": channel,
        "step": 1,
        "description": None,
        "description_entities": None,
        "photo": None,
        "button_text": "🎁 Участвовать",
        "button_color": "default",
        "winners_count": 1,
        "end_type": None,
        "end_time": None,
        "target_participants": None,
        "required_channel_ids": [],
        "created_at": datetime.datetime.now().isoformat()
    }
    step_1_post(message.chat.id, user_id)

def step_1_post(chat_id, user_id):
    text = "<b>💬 [1/8] Пост розыгрыша</b>\n<blockquote>Отправьте пост, который будет опубликован в канале</blockquote>"
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("‹ Назад", callback_data=f"back_step_{user_id}_0"))
    sent = bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)
    add_to_delete(chat_id, sent.message_id)
    bot.register_next_step_handler_by_chat_id(chat_id, process_post_message, user_id)

def process_post_message(message, user_id):
    if message.text and (message.text.startswith('/create') or message.text.startswith('/start') or message.text.startswith('/postlot')):
        delete_message(message.chat.id, message.message_id)
        if user_id in temp_data:
            clear_temp(user_id)
        main_menu(message.chat.id, user_id)
        return
    
    if user_id not in temp_data:
        bot.reply_to(message, "❌ <b>Данные потеряны. Начните заново через /start</b>", parse_mode="HTML")
        main_menu(message.chat.id, user_id)
        return
    
    if message.text:
        temp_data[user_id]["description"] = message.text
        if message.entities:
            temp_data[user_id]["description_entities"] = [e.to_dict() for e in message.entities]
        else:
            temp_data[user_id]["description_entities"] = None
        temp_data[user_id]["photo"] = None
    elif message.photo:
        temp_data[user_id]["description"] = message.caption or ""
        if message.caption_entities:
            temp_data[user_id]["description_entities"] = [e.to_dict() for e in message.caption_entities]
        else:
            temp_data[user_id]["description_entities"] = None
        temp_data[user_id]["photo"] = message.photo[-1].file_id
    else:
        bot.reply_to(message, "❌ <b>Отправьте текст или фото с подписью</b>", parse_mode="HTML")
        step_1_post(message.chat.id, user_id)
        return
    
    step_2_button(message.chat.id, user_id)

def step_2_button(chat_id, user_id):
    text = "<b>💬 [2/8] Кнопка к посту</b>\n\n<blockquote>Выберите готовый вариант или напишите свой текст кнопки</blockquote>"
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("Участвовать", callback_data=f"btn_{user_id}_Участвовать"),
        types.InlineKeyboardButton("Принять участие", callback_data=f"btn_{user_id}_Принять_участие"),
        types.InlineKeyboardButton("Я участвую!", callback_data=f"btn_{user_id}_Я_участвую!"),
        types.InlineKeyboardButton("Свой текст", callback_data=f"btn_custom_{user_id}"),
        types.InlineKeyboardButton("Назад", callback_data=f"back_step_{user_id}_1")
    )
    sent = bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)
    add_to_delete(chat_id, sent.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("btn_"))
def btn_callback(call):
    data = call.data
    
    if data.startswith("btn_custom_"):
        try:
            user_id = int(data.split("_")[2])
        except:
            bot.answer_callback_query(call.id, "❌ Ошибка")
            return
        if call.from_user.id != user_id:
            bot.answer_callback_query(call.id, "❌ Это не ваш розыгрыш")
            return
        bot.answer_callback_query(call.id)
        msg = bot.send_message(call.message.chat.id, "✏️ <b>Введите ваш текст кнопки (макс 30 символов):</b>", parse_mode="HTML")
        add_to_delete(call.message.chat.id, msg.message_id)
        bot.register_next_step_handler(msg, process_custom_button, user_id)
        return
    
    parts = data.split("_")
    if len(parts) >= 3:
        try:
            user_id = int(parts[1])
            if call.from_user.id != user_id:
                bot.answer_callback_query(call.id, "❌ Это не ваш розыгрыш")
                return
            button_text = "_".join(parts[2:]).replace("_", " ")
            if user_id not in temp_data:
                bot.answer_callback_query(call.id, "❌ Данные потеряны. Начните заново")
                main_menu(call.message.chat.id, user_id)
                return
            temp_data[user_id]["button_text"] = button_text
            bot.answer_callback_query(call.id, f"✅ Текст кнопки: {button_text}")
            step_3_color(call.message.chat.id, user_id)
        except ValueError:
            bot.answer_callback_query(call.id, "❌ Ошибка")
    else:
        bot.answer_callback_query(call.id, "❌")

def process_custom_button(message, user_id):
    text = message.text.strip()[:30]
    if not text:
        text = "Участвовать"
    if user_id not in temp_data:
        bot.reply_to(message, "❌ <b>Данные потеряны. Начните заново</b>", parse_mode="HTML")
        main_menu(message.chat.id, user_id)
        return
    temp_data[user_id]["button_text"] = text
    step_3_color(message.chat.id, user_id)

def step_3_color(chat_id, user_id):
    text = "<b>💬 [3/8] Цвет кнопки</b>\n\n<blockquote>Выберите цвет кнопки участия</blockquote>"
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        create_colored_button("Обычный", f"color_{user_id}_default", color="default"),
        create_colored_button("Синий", f"color_{user_id}_primary", color="primary"),
        create_colored_button("Красный", f"color_{user_id}_danger", color="danger"),
        create_colored_button("Зелёный", f"color_{user_id}_success", color="success"),
        create_colored_button("Назад", f"back_step_{user_id}_2", color="default")
    )
    sent = bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)
    add_to_delete(chat_id, sent.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("color_"))
def color_callback(call):
    parts = call.data.split("_")
    user_id = int(parts[1])
    color = parts[2]
    
    if call.from_user.id != user_id:
        bot.answer_callback_query(call.id, "❌ Это не ваш розыгрыш")
        return
    
    if user_id not in temp_data:
        bot.answer_callback_query(call.id, "❌ Данные потеряны. Начните заново")
        main_menu(call.message.chat.id, user_id)
        return
    
    temp_data[user_id]["button_color"] = color
    bot.answer_callback_query(call.id, f"Выбран цвет: {color}")
    step_4_winners(call.message.chat.id, user_id)

def step_4_winners(chat_id, user_id):
    text = "<b>💬 [4/8] Количество победителей</b>\n\n<blockquote>Введите количество победителей (от 1 до 100)</blockquote>"
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("‹️ Назад", callback_data=f"back_step_{user_id}_3"))
    sent = bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)
    add_to_delete(chat_id, sent.message_id)
    bot.register_next_step_handler_by_chat_id(chat_id, process_winners_count, user_id)

def process_winners_count(message, user_id):
    if user_id not in temp_data:
        bot.reply_to(message, "❌ <b>Данные потеряны. Начните заново</b>", parse_mode="HTML")
        main_menu(message.chat.id, user_id)
        return
    try:
        cnt = int(message.text.strip())
        if cnt < 1 or cnt > 100:
            raise ValueError
    except:
        bot.reply_to(message, "❌ <b>Введите число от 1 до 100</b>", parse_mode="HTML")
        step_4_winners(message.chat.id, user_id)
        return
    temp_data[user_id]["winners_count"] = cnt
    step_5_end_type(message.chat.id, user_id)

def step_5_end_type(chat_id, user_id):
    text = "<b>💬 [5/8] Как подвести итоги</b>\n\n"
    text += "<blockquote> <b>По времени</b> — итоги в заданную дату\n"
    text += " <b>По числу участников</b> — итоги, когда наберётся нужное количество</blockquote>"
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("⏰ По времени", callback_data=f"endtype_{user_id}_time"),
        types.InlineKeyboardButton("👥 По числу участников", callback_data=f"endtype_{user_id}_participants"),
        types.InlineKeyboardButton("‹ Назад", callback_data=f"back_step_{user_id}_4")
    )
    sent = bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)
    add_to_delete(chat_id, sent.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("endtype_"))
def endtype_callback(call):
    parts = call.data.split("_")
    if len(parts) < 3:
        bot.answer_callback_query(call.id, "❌ Ошибка")
        return
    try:
        user_id = int(parts[1])
        end_type = parts[2]
    except:
        bot.answer_callback_query(call.id, "❌ Ошибка")
        return
    
    if call.from_user.id != user_id:
        bot.answer_callback_query(call.id, "❌ Не ваш розыгрыш")
        return
    if user_id not in temp_data:
        bot.answer_callback_query(call.id, "❌ Данные потеряны. Начните заново")
        main_menu(call.message.chat.id, user_id)
        return
    temp_data[user_id]["end_type"] = end_type
    bot.answer_callback_query(call.id)
    if end_type == "time":
        msg = bot.send_message(call.message.chat.id, "⏰ <b>Введите дату и время окончания в формате:</b>\n<code>2025-12-31 23:59</code>", parse_mode="HTML")
        add_to_delete(call.message.chat.id, msg.message_id)
        bot.register_next_step_handler(msg, process_end_time, user_id)
    else:
        msg = bot.send_message(call.message.chat.id, "👥 <b>Введите количество участников для завершения розыгрыша:</b>", parse_mode="HTML")
        add_to_delete(call.message.chat.id, msg.message_id)
        bot.register_next_step_handler(msg, process_target_participants, user_id)

def process_end_time(message, user_id):
    if user_id not in temp_data:
        bot.reply_to(message, "❌ <b>Данные потеряны. Начните заново</b>", parse_mode="HTML")
        main_menu(message.chat.id, user_id)
        return
    try:
        dt = datetime.datetime.strptime(message.text.strip(), "%Y-%m-%d %H:%M")
        if dt < datetime.datetime.now():
            raise ValueError
        temp_data[user_id]["end_time"] = dt
        temp_data[user_id]["target_participants"] = None
    except:
        bot.reply_to(message, "❌ <b>Неверный формат. Пример: 2026-12-31 23:59</b>", parse_mode="HTML")
        step_5_end_type(message.chat.id, user_id)
        return
    step_6_subscription(message.chat.id, user_id)

def process_target_participants(message, user_id):
    if user_id not in temp_data:
        bot.reply_to(message, "❌ <b>Данные потеряны. Начните заново</b>", parse_mode="HTML")
        main_menu(message.chat.id, user_id)
        return
    try:
        target = int(message.text.strip())
        if target < 1 or target > 10000000:
            raise ValueError
        temp_data[user_id]["target_participants"] = target
        temp_data[user_id]["end_time"] = None
    except:
        bot.reply_to(message, "❌ <b>Введите число от 1 до 10 000 000</b>", parse_mode="HTML")
        step_5_end_type(message.chat.id, user_id)
        return
    step_6_subscription(message.chat.id, user_id)

def step_6_subscription(chat_id, user_id):
    if user_id not in temp_data:
        main_menu(chat_id, user_id)
        return
    data = temp_data[user_id]
    
    if "required_channel_ids" not in data:
        data["required_channel_ids"] = []
    
    text = "<b>💬 [6/8] Обязательная подписка</b>\n\n"
    text += "<blockquote>До 5 каналов для обязательной подписки.</blockquote>\n"
    text += "<blockquote>Если она не нужна — нажмите «Пропустить»</blockquote>\n\n"
        
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton("➕ Добавить канал", callback_data=f"add_reqchan_{user_id}"))
    
    if data["required_channel_ids"]:
        markup.add(types.InlineKeyboardButton("Очистить все", callback_data=f"clear_all_reqchan_{user_id}"))
        markup.add(types.InlineKeyboardButton("➡️ Далее", callback_data=f"next_confirm_{user_id}"))
    else:
        markup.add(types.InlineKeyboardButton("⏭ Пропустить", callback_data=f"next_confirm_{user_id}"))
    
    markup.add(types.InlineKeyboardButton("‹ Назад", callback_data=f"back_step_{user_id}_5"))
    
    sent = bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)
    add_to_delete(chat_id, sent.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("add_reqchan_"))
def add_reqchan(call):
    try:
        user_id = int(call.data.split("_")[2])
    except:
        bot.answer_callback_query(call.id, "❌ Ошибка")
        return
    if user_id not in temp_data:
        bot.answer_callback_query(call.id, "❌ Данные потеряны. Начните заново")
        main_menu(call.message.chat.id, user_id)
        return
    
    data = temp_data[user_id]
    if "required_channel_ids" not in data:
        data["required_channel_ids"] = []
    
    if len(data["required_channel_ids"]) >= 5:
        bot.answer_callback_query(call.id, "❌ Максимум 5 каналов для подписки!", show_alert=True)
        return
    
    bot.answer_callback_query(call.id)
    delete_previous_messages(call.message.chat.id, None)
    
    text = (
        "<b>➕ Добавление канала для обязательной подписки</b>\n\n"
        "<blockquote>1️⃣ Выдайте боту права администратора в канале\n"
        "2️⃣ Отправьте @username канала\n"
        "3️⃣ ИЛИ перешлите любое сообщение из канала</blockquote>\n\n"
        f"<i>Осталось мест: {5 - len(data['required_channel_ids'])}</i>"
    )
    msg = bot.send_message(call.message.chat.id, text, parse_mode="HTML")
    add_to_delete(call.message.chat.id, msg.message_id)
    bot.register_next_step_handler(msg, process_add_channel_by_username, user_id)

def process_add_channel_by_username(message, user_id):
    if user_id not in temp_data:
        bot.send_message(message.chat.id, "❌ <b>Данные потеряны. Начните заново</b>", parse_mode="HTML")
        main_menu(message.chat.id, user_id)
        return
    
    delete_message(message.chat.id, message.message_id)
    
    text = message.text.strip().lower() if message.text else ""
    if text in ['достаточно каналов', 'идем дальше', 'далее', 'готово', 'next', 'done', 'хватит', 'всё', 'дальше', 'продолжить', 'пропустить']:
        step_7_confirm(message.chat.id, user_id)
        return
    
    data = temp_data[user_id]
    if "required_channel_ids" not in data:
        data["required_channel_ids"] = []
    
    if len(data["required_channel_ids"]) >= 5:
        bot.send_message(message.chat.id, "❌ <b>Максимум 5 каналов уже добавлено!</b>", parse_mode="HTML")
        step_7_confirm(message.chat.id, user_id)
        return
    
    channel_id = None
    display_name = None
    
    if message.forward_from_chat:
        chat = message.forward_from_chat
        channel_id = str(chat.id)
        display_name = chat.title if chat.title else str(chat.id)
        try:
            bot.get_chat_member(chat.id, bot.get_me().id)
        except:
            bot.send_message(message.chat.id, "❌ <b>Бот не администратор!</b>", parse_mode="HTML")
            return
    elif message.text and message.text.strip().startswith('@'):
        channel_input = message.text.strip()
        try:
            chat = bot.get_chat(channel_input)
            bot.get_chat_member(chat.id, bot.get_me().id)
            channel_id = str(chat.id)
            display_name = chat.title if chat.title else channel_input
        except:
            bot.send_message(message.chat.id, f"❌ <b>Канал {channel_input} не найден или бот не администратор!</b>", parse_mode="HTML")
            return
    else:
        bot.send_message(message.chat.id, "❌ <b>Отправьте @username канала или перешлите сообщение</b>", parse_mode="HTML")
        msg = bot.send_message(message.chat.id, "Попробуйте снова:", parse_mode="HTML")
        add_to_delete(message.chat.id, msg.message_id)
        bot.register_next_step_handler(msg, process_add_channel_by_username, user_id)
        return
    
    if str(channel_id) in [str(x) for x in data["required_channel_ids"]]:
        bot.send_message(message.chat.id, f"⚠️ <b>Канал {display_name} уже добавлен!</b>", parse_mode="HTML")
        msg = bot.send_message(message.chat.id, "Отправьте другой канал:", parse_mode="HTML")
        add_to_delete(message.chat.id, msg.message_id)
        bot.register_next_step_handler(msg, process_add_channel_by_username, user_id)
        return
    
    data["required_channel_ids"].append(channel_id)
    temp_data[user_id] = data
    added_count = len(data["required_channel_ids"])
    
    # ЭТУ КНОПКУ МЕНЯЕМ НА ЗЕЛЁНУЮ
    markup = types.InlineKeyboardMarkup()
    markup.add(create_colored_button("✅ Достаточно каналов, идем дальше", f"finish_channels_{user_id}", color="success"))
    
    bot.send_message(
        message.chat.id, 
        f"✅ <b>Канал {display_name} добавлен! ({added_count}/5)</b>\n\n"
        f"<blockquote>Можете добавить еще или нажать кнопку</blockquote>",
        parse_mode="HTML",
        reply_markup=markup
    )
    
    if added_count >= 5:
        step_7_confirm(message.chat.id, user_id)
        return
    
    bot.register_next_step_handler(message, process_add_channel_by_username, user_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("clear_all_reqchan_"))
def clear_all_reqchan(call):
    try:
        user_id = int(call.data.split("_")[3])
    except:
        bot.answer_callback_query(call.id, "❌ Ошибка")
        return
    
    if user_id not in temp_data:
        bot.answer_callback_query(call.id, "❌ Данные потеряны")
        main_menu(call.message.chat.id, user_id)
        return
    
    temp_data[user_id]["required_channel_ids"] = []
    bot.answer_callback_query(call.id, "✅ Все каналы удалены")
    step_6_subscription(call.message.chat.id, user_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("finish_channels_"))
def finish_channels(call):
    try:
        user_id = int(call.data.split("_")[2])
    except:
        bot.answer_callback_query(call.id, "❌ Ошибка")
        return
    
    if call.from_user.id != user_id:
        bot.answer_callback_query(call.id, "❌ Не ваше действие", show_alert=True)
        return
    
    if user_id not in temp_data:
        bot.answer_callback_query(call.id, "❌ Данные потеряны")
        main_menu(call.message.chat.id, user_id)
        return
    
    bot.answer_callback_query(call.id, "Я закончил")
    step_7_confirm(call.message.chat.id, user_id)

@bot.callback_query_handler(func=lambda call: call.data == "back_to_main_menu")
def back_to_main_menu(call):
    user_id = call.from_user.id
    delete_previous_messages(call.message.chat.id, user_id)
    main_menu(call.message.chat.id, user_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("back_step_"))
def back_step(call):
    parts = call.data.split("_")
    if len(parts) < 4:
        bot.answer_callback_query(call.id, "❌ Ошибка")
        return
    try:
        user_id = int(parts[2])
        step = int(parts[3])
    except:
        bot.answer_callback_query(call.id, "❌ Ошибка")
        return
    
    if call.from_user.id != user_id:
        bot.answer_callback_query(call.id, "❌ Не ваш розыгрыш", show_alert=True)
        return
    
    bot.answer_callback_query(call.id)
    chat_id = call.message.chat.id
    
    if user_id not in temp_data and step > 0:
        bot.send_message(chat_id, "❌ <b>Данные потеряны. Начните заново через /start</b>", parse_mode="HTML")
        main_menu(chat_id, user_id)
        return
    
    if step == 0:
        main_menu(chat_id, user_id)
    elif step == 1:
        step_1_post(chat_id, user_id)
    elif step == 2:
        step_2_button(chat_id, user_id)
    elif step == 3:
        step_3_color(chat_id, user_id)
    elif step == 4:
        step_4_winners(chat_id, user_id)
    elif step == 5:
        step_5_end_type(chat_id, user_id)
    elif step == 6:
        step_6_subscription(chat_id, user_id)
    elif step == 7:
        step_7_confirm(chat_id, user_id)

def step_7_confirm(chat_id, user_id):
    if user_id not in temp_data:
        main_menu(chat_id, user_id)
        return
    data = temp_data[user_id]
    
    text = "‼️ <b>Внимательно перепроверьте конкурс</b>\n\n"
    
    if data.get("target_participants"):
        text += f"<blockquote>🔚 <b>Конкурс завершится, когда количество участников станет равно {data['target_participants']}</b></blockquote>\n"
    elif data.get("end_time"):
        text += f"<blockquote>🔚 <b>Конкурс завершится: {data['end_time'].strftime('%d.%m.%Y %H:%M')}</b></blockquote>\n"
    
    text += f"🏆 <b>Количество победителей:</b> {data['winners_count']}\n\n"
    text += "\n<b>Всё верно? Нажмите \"✅ Подтвердить\".</b>"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(
        create_colored_button("✅ Подтвердить", f"confirm_{user_id}", color="success"),
        create_colored_button("❌ Отменить", "cancel_create", color="danger")
    )
    sent = bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)
    add_to_delete(chat_id, sent.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("next_confirm_"))
def next_confirm(call):
    try:
        user_id = int(call.data.split("_")[2])
    except:
        bot.answer_callback_query(call.id, "❌ Ошибка")
        return
    if user_id not in temp_data:
        bot.answer_callback_query(call.id, "❌ Данные потеряны")
        main_menu(call.message.chat.id, user_id)
        return
    step_7_confirm(call.message.chat.id, user_id)

@bot.callback_query_handler(func=lambda call: call.data == "cancel_create")
def cancel_create(call):
    user_id = call.from_user.id
    if user_id in temp_data:
        clear_temp(user_id)
    bot.answer_callback_query(call.id, "❌ Создание отменено")
    try:
        bot.edit_message_text("❌ <b>Создание розыгрыша отменено</b>", call.message.chat.id, call.message.message_id, parse_mode="HTML")
    except:
        pass
    main_menu(call.message.chat.id, user_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_"))
def confirm_giveaway(call):
    try:
        user_id = int(call.data.split("_")[1])
    except:
        bot.answer_callback_query(call.id, "❌ Ошибка")
        return
    
    if call.from_user.id != user_id:
        bot.answer_callback_query(call.id, "❌ Это не ваш розыгрыш!", show_alert=True)
        return
    
    if user_id not in temp_data:
        bot.answer_callback_query(call.id, "❌ Данные потеряны, начните заново /start")
        main_menu(call.message.chat.id, user_id)
        return
    
    bot.answer_callback_query(call.id, "✅ Розыгрыш создаётся...")
    try:
        bot.edit_message_text("✅ <b>Пост будет опубликован в этот канал в ближайшее время!</b>", call.message.chat.id, call.message.message_id, parse_mode="HTML")
    except:
        pass
    
    publish_giveaway(user_id, temp_data[user_id])

def publish_giveaway(user_id, data):
    giveaway_id = generate_giveaway_id()
    postlot_key = generate_postlot_key(giveaway_id)
    
    button_text = data['button_text']
    button_color = data.get('button_color', 'default')
    
    color_map = {
        "default": None,
        "primary": "primary",
        "danger": "danger",
        "success": "success"
    }
    
    button = types.InlineKeyboardButton(
        text=button_text,
        url=f"https://t.me/{BOT_USERNAME}?start=giveaway_{giveaway_id}",
        style=color_map.get(button_color)
    )
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(button)
    
    full_text = data["description"]
    caption_entities = data.get("description_entities")
    
    if caption_entities:
        restored_entities = []
        for e in caption_entities:
            entity = types.MessageEntity(
                type=e.get('type'),
                offset=e.get('offset'),
                length=e.get('length'),
                url=e.get('url'),
                user=e.get('user'),
                language=e.get('language')
            )
            restored_entities.append(entity)
        caption_entities = restored_entities
    
    required_channel_ids = data.get("required_channel_ids", [])
    final_invite_links = []
    
    for channel_id_str in required_channel_ids:
        try:
            channel_id = int(channel_id_str)
            invite_link = bot.create_chat_invite_link(
                chat_id=channel_id,
                name=f"giveaway_{giveaway_id}",
                member_limit=10000000000000000000000,
                creates_join_request=False
            )
            final_invite_links.append(invite_link.invite_link)
            print(f"✅ Создана ссылка для канала {channel_id}")
        except Exception as e:
            print(f"⚠️ Не удалось создать ссылку: {e}")
            final_invite_links.append(f"Канал {channel_id_str}")
    
    try:
        if data["photo"]:
            sent_msg = bot.send_photo(
                data["channel"],
                data["photo"],
                caption=full_text,
                caption_entities=caption_entities,
                reply_markup=markup,
                parse_mode=None
            )
        else:
            sent_msg = bot.send_message(
                data["channel"],
                full_text,
                entities=caption_entities,
                reply_markup=markup,
                parse_mode=None
            )
        
        giveaway_data = {
            "giveaway_id": giveaway_id,
            "chat_id": sent_msg.chat.id,
            "message_id": sent_msg.message_id,
            "creator_id": user_id,
            "channel": data["channel"],
            "description": data["description"],
            "description_entities": data.get("description_entities"),
            "button_text": data["button_text"],
            "button_color": data.get("button_color"),
            "photo": data["photo"],
            "winners_count": data["winners_count"],
            "participants": [],
            "end_time": data.get("end_time"),
            "target_participants": data.get("target_participants"),
            "required_channel_ids": required_channel_ids,
            "required_invite_links": final_invite_links,
            "is_active": True,
            "created_at": datetime.datetime.now().isoformat(),
            "postlot_key": postlot_key
        }
        
        if data.get("end_time"):
            giveaway_data["end_time"] = data["end_time"].isoformat() if isinstance(data["end_time"], datetime.datetime) else data["end_time"]
        
        db["giveaways"][giveaway_id] = giveaway_data
        
        if data.get("end_time"):
            delay = (data["end_time"] - datetime.datetime.now()).total_seconds()
            if delay > 0:
                threading.Timer(delay, conclude_giveaway, args=[giveaway_id]).start()
        
        update_user_stats(user_id, created=True)
        save_db(db)
        
        bot.send_message(
            user_id,
            f"<blockquote>✅ <b>Розыгрыш успешно создан!</b></blockquote>\n\n"
            f"<code>/postlot{postlot_key}</code>\n\n"
            f"<i>Отправьте эту команду в бота, чтобы опубликовать розыгрыш в другом канале</i>",
            parse_mode="HTML"
        )
        
        clear_temp(user_id)
        
    except Exception as e:
        bot.send_message(user_id, f"❌ <b>Ошибка при публикации:</b>\n<code>{str(e)[:200]}</code>", parse_mode="HTML")
        print(f"Ошибка публикации: {e}")

# ================= ПОСТ-ЛОТ =================

@bot.message_handler(func=lambda msg: msg.text and msg.text.startswith('/postlot'))
def handle_postlot(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    text = message.text.strip()
    
    delete_message(chat_id, message.message_id)
    
    if text.startswith('/postlot '):
        key = text.replace('/postlot ', '')
    else:
        key = text.replace('/postlot', '')
    
    found_giveaway = None
    found_giveaway_id = None
    
    for g_id, g in db["giveaways"].items():
        if g.get("postlot_key") == key:
            found_giveaway = g
            found_giveaway_id = g_id
            break
    
    if not found_giveaway:
        sent = bot.reply_to(message, "❌ <b>Недействительный ключ или розыгрыш не найден!</b>", parse_mode="HTML")
        add_to_delete(chat_id, sent.message_id)
        return
    
    channels = get_user_channels(user_id)
    
    if not channels:
        sent = bot.send_message(chat_id, "❌ <b>У вас нет добавленных каналов!</b>", parse_mode="HTML")
        add_to_delete(chat_id, sent.message_id)
        return
    
    text = "📢 <b>Выберите канал для публикации</b>"
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    for channel in channels:
        channel_title = get_channel_display_name(channel)
        markup.add(types.InlineKeyboardButton(f"{channel_title}", callback_data=f"postlot_channel_{channel}_{found_giveaway_id}_{user_id}"))
    
    markup.add(types.InlineKeyboardButton("❌ Отмена", callback_data="cancel_postlot"))
    
    sent = bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)
    add_to_delete(chat_id, sent.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("postlot_channel_"))
def postlot_channel_callback(call):
    parts = call.data.split("_")
    if len(parts) >= 4:
        channel = "_".join(parts[2:-2])
        giveaway_id = parts[-2]
        user_id = int(parts[-1])
    else:
        bot.answer_callback_query(call.id, "❌ Ошибка формата", show_alert=True)
        return
    
    if call.from_user.id != user_id:
        bot.answer_callback_query(call.id, "❌ Не ваш запрос", show_alert=True)
        return
    
    if giveaway_id not in db["giveaways"]:
        bot.answer_callback_query(call.id, "❌ Розыгрыш не найден", show_alert=True)
        return
    
    giveaway = db["giveaways"][giveaway_id]
    
    user_channels = get_user_channels(user_id)
    if channel not in user_channels:
        bot.answer_callback_query(call.id, "❌ У вас нет доступа к этому каналу!", show_alert=True)
        return
    
    bot.answer_callback_query(call.id, "✅ Публикую...")
    
    button_text = giveaway["button_text"]
    button_color = giveaway.get("button_color", "default")
    
    color_map = {
        "default": None,
        "primary": "primary",
        "danger": "danger",
        "success": "success"
    }
    
    button = types.InlineKeyboardButton(
        text=button_text,
        url=f"https://t.me/{BOT_USERNAME}?start=giveaway_{giveaway_id}",
        style=color_map.get(button_color)
    )
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(button)
    
    full_text = giveaway["description"]
    caption_entities = giveaway.get("description_entities")
    
    if caption_entities:
        restored_entities = []
        for e in caption_entities:
            entity = types.MessageEntity(
                type=e.get('type'),
                offset=e.get('offset'),
                length=e.get('length'),
                url=e.get('url'),
                user=e.get('user'),
                language=e.get('language')
            )
            restored_entities.append(entity)
        caption_entities = restored_entities
    
    try:
        if giveaway.get("photo"):
            bot.send_photo(
                channel,
                giveaway["photo"],
                caption=full_text,
                caption_entities=caption_entities,
                reply_markup=markup,
                parse_mode="HTML"
            )
        else:
            if caption_entities:
                bot.send_message(
                    channel,
                    full_text,
                    entities=caption_entities,
                    reply_markup=markup,
                    parse_mode=None
                )
            else:
                bot.send_message(
                    channel,
                    full_text,
                    reply_markup=markup,
                    parse_mode="HTML"
                )
        
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        
        bot.send_message(call.message.chat.id, "✅ <b>Розыгрыш опубликован!</b>", parse_mode="HTML")
        
    except Exception as e:
        error_msg = str(e)
        if "chat not found" in error_msg:
            bot.send_message(
                call.message.chat.id,
                "❌ <b>Ошибка: Бот не является администратором канала или канал не найден!</b>\n\n"
                "Добавьте бота в канал как администратора и попробуйте снова.",
                parse_mode="HTML"
            )
        else:
            bot.send_message(
                call.message.chat.id,
                f"❌ <b>Ошибка:</b>\n<code>{error_msg[:200]}</code>",
                parse_mode="HTML"
            )

@bot.callback_query_handler(func=lambda call: call.data == "cancel_postlot")
def cancel_postlot(call):
    bot.answer_callback_query(call.id, "❌ Отменено")
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass
    main_menu(call.message.chat.id, call.from_user.id)

# ================= ПРОВЕРКА ПОДПИСКИ (РАБОТАЕТ) =================

@bot.callback_query_handler(func=lambda call: call.data.startswith("check_"))
def check_subscription_callback(call):
    parts = call.data.split("_")
    if len(parts) < 2:
        bot.answer_callback_query(call.id, "❌ Ошибка формата", show_alert=True)
        return
    
    giveaway_id = parts[1]
    user_id = call.from_user.id
    
    if giveaway_id not in db["giveaways"]:
        bot.answer_callback_query(call.id, "❌ Розыгрыш не найден в базе данных!", show_alert=True)
        return
    
    giveaway = db["giveaways"][giveaway_id]
    
    if not giveaway.get("is_active"):
        bot.answer_callback_query(call.id, "⏰ Этот розыгрыш уже завершён!", show_alert=True)
        return
    
    if user_id in giveaway.get("participants", []):
        bot.answer_callback_query(call.id, "✅ Вы уже участвуете в розыгрыше!", show_alert=True)
        return
    
    required_channel_ids = giveaway.get("required_channel_ids", [])
    
    if not required_channel_ids:
        giveaway["participants"].append(user_id)
        save_db(db)
        update_participation_button(giveaway)
        
        if giveaway.get("target_participants") and len(giveaway["participants"]) >= giveaway["target_participants"]:
            conclude_giveaway(giveaway_id)
        
        bot.answer_callback_query(call.id, "✅ Вы успешно участвуете в розыгрыше!", show_alert=True)
        bot.send_message(user_id, "🎉 <b>Вы участвуете в розыгрыше!</b>", parse_mode="HTML")
        
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        return
    
    all_subscribed = True
    for channel_id_str in required_channel_ids:
        try:
            channel_id = int(channel_id_str)
            member = bot.get_chat_member(channel_id, user_id)
            if member.status in ['left', 'kicked']:
                all_subscribed = False
                break
        except:
            all_subscribed = False
            break
    
    if all_subscribed:
        if user_id not in giveaway["participants"]:
            giveaway["participants"].append(user_id)
            save_db(db)
            update_participation_button(giveaway)
            
            if giveaway.get("target_participants") and len(giveaway["participants"]) >= giveaway["target_participants"]:
                conclude_giveaway(giveaway_id)
            
            bot.answer_callback_query(call.id, "✅ Вы успешно подписаны и участвуете в розыгрыше!", show_alert=True)
            bot.send_message(user_id, "🎉 <b>Вы участвуете в розыгрыше!</b>", parse_mode="HTML")
            
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                pass
        else:
            bot.answer_callback_query(call.id, "✅ Вы уже участвуете в розыгрыше!", show_alert=True)
    else:
        bot.answer_callback_query(call.id, "❌ Вы не подписаны на все обязательные каналы!", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith("manual_join_"))
def manual_join_callback(call):
    parts = call.data.split("_")
    if len(parts) < 3:
        bot.answer_callback_query(call.id, "❌ Ошибка формата", show_alert=True)
        return
    
    giveaway_id = parts[2]
    user_id = call.from_user.id
    
    if giveaway_id not in db["giveaways"]:
        bot.answer_callback_query(call.id, "❌ Розыгрыш не найден в базе данных!", show_alert=True)
        return
    
    giveaway = db["giveaways"][giveaway_id]
    
    if not giveaway.get("is_active"):
        bot.answer_callback_query(call.id, "⏰ Этот розыгрыш уже завершён!", show_alert=True)
        return
    
    if user_id in giveaway.get("participants", []):
        bot.answer_callback_query(call.id, "✅ Вы уже участвуете в розыгрыше!", show_alert=True)
        return
    
    giveaway["participants"].append(user_id)
    save_db(db)
    update_participation_button(giveaway)
    
    if giveaway.get("target_participants") and len(giveaway["participants"]) >= giveaway["target_participants"]:
        conclude_giveaway(giveaway_id)
    
    bot.answer_callback_query(call.id, "✅ Вы успешно участвуете в розыгрыше!", show_alert=True)
    bot.send_message(user_id, "🎉 <b>Вы участвуете в розыгрыше!</b>", parse_mode="HTML")
    
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass

@bot.callback_query_handler(func=lambda call: call.data == "main_create_giveaway")
def main_create_giveaway_callback(call):
    user_id = call.from_user.id
    get_user(user_id)
    delete_message(call.message.chat.id, call.message.message_id)
    
    if user_id in temp_data:
        clear_temp(user_id)
    
    channels = get_user_channels(user_id)
    
    text = "🧰 <b>Создание розыгрыша</b>\n\n<blockquote>Выберите канал, где будет опубликован пост розыгрыша</blockquote>"
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    colors = ["primary", "success", "default", "danger"]
    for idx, channel in enumerate(channels):
        channel_title = channel
        try:
            chat = bot.get_chat(channel)
            if chat.title:
                channel_title = chat.title
        except:
            pass
        color = colors[idx % len(colors)]
        markup.add(create_colored_button(f"{channel_title}", f"select_channel_{channel}_{user_id}", color=color))
    
    markup.add(create_colored_button("➕ Добавить канал", "add_channel_step", color="success"))
    
    sent = bot.send_message(call.message.chat.id, text, parse_mode="HTML", reply_markup=markup)
    add_to_delete(call.message.chat.id, sent.message_id)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "main_my_giveaways")
def main_my_giveaways_callback(call):
    # Создаем объект сообщения для существующей функции
    class FakeMessage:
        def __init__(self, chat_id, from_user, message_id):
            self.chat = type('obj', (object,), {'id': chat_id})
            self.from_user = from_user
            self.message_id = message_id
    
    fake_msg = FakeMessage(call.message.chat.id, call.from_user, call.message.message_id)
    menu_my_giveaways(fake_msg)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "main_my_channels")
def main_my_channels_callback(call):
    class FakeMessage:
        def __init__(self, chat_id, from_user, message_id):
            self.chat = type('obj', (object,), {'id': chat_id})
            self.from_user = from_user
            self.message_id = message_id
    
    fake_msg = FakeMessage(call.message.chat.id, call.from_user, call.message.message_id)
    menu_my_channels(fake_msg)
    bot.answer_callback_query(call.id)
if __name__ == '__main__':
    if not os.path.exists('data'):
        os.makedirs('data')
    
    print(f"✅ Бот @{BOT_USERNAME} запущен!")
    print(f"💪 Обработка callback-запросов включена")
    
    import signal
    
    def signal_handler(signum, frame):
        print("\n🛑 Останавливаем бота...")
        bot.stop_polling()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Запуск с правильной обработкой callback'ов
    try:
        bot.infinity_polling(
            timeout=60,
            skip_pending=True
        )
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        traceback.print_exc()