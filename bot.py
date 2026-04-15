import os
import logging
import tempfile
import base64
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

authorized_users = {}
FREE_USES = 1

WELCOME_TEXT = """👋 Привет! Я транскрибирую видео и аудио в текст.

🎯 Форматы: MP4, MKV, AVI, MOV, MP3, WAV, M4A и другие
⚠️ Лимит Telegram: до 20 МБ на файл

💳 Тарифы:
• 1 бесплатная транскрипция
• Пробный — 1000₽ (10 транскрипций)
• Навсегда — 2000₽ (безлимит)

Пришли файл и я начну! 🚀"""


def is_authorized(uid):
    if uid == ADMIN_ID: return True
    u = authorized_users.get(uid)
    if not u: return True
    if u["plan"] == "full": return True
    return u["uses_left"] > 0

def get_uses_left(uid):
    if uid == ADMIN_ID: return "∞ (админ)"
    u = authorized_users.get(uid)
    if not u: return f"{FREE_USES} бесплатных"
    if u["plan"] == "full": return "∞ безлимит"
    return f"{u['uses_left']} осталось"

def consume_use(uid):
    if uid == ADMIN_ID: return
    u = authorized_users.get(uid)
    if not u:
        authorized_users[uid] = {"plan": "free", "uses_left": 0}
    elif u["plan"] in ("free", "trial"):
        authorized_users[uid]["uses_left"] = max(0, u["uses_left"] - 1)

async def transcribe_gemini(file_path, filename):
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else "mp3"
    mime_map = {
        "mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4",
        "ogg": "audio/ogg", "flac": "audio/flac", "aac": "audio/aac",
        "mp4": "video/mp4", "mkv": "video/x-matroska", "avi": "video/x-msvideo",
        "mov": "video/quicktime", "webm": "video/webm",
    }
    mime = mime_map.get(ext, "audio/mpeg")
    with open(file_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    body = {"contents": [{"parts": [
        {"inline_data": {"mime_type": mime, "data": b64}},
        {"text": "Транскрибируй дословно. Расставь знаки препинания. Верни только текст."}
    ]}]}
    for model in ["gemini-2.0-flash", "gemini-1.5-flash-latest"]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        async with httpx.AsyncClient(timeout=300) as client:
            try:
                r = await client.post(url, json=body)
                if r.status_code == 404: continue
                r.raise_for_status()
                return r.json()["candidates"][0]["content"]["parts"][0]["text"]
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404: continue
                raise Exception(f"Gemini {e.response.status_code}: {e.response.text[:200]}")
    raise Exception("Gemini недоступен")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_TEXT)

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(f"👤 ID: `{uid}`\n💳 Осталось: {get_uses_left(uid)}\n\n/buy — купить доступ", parse_mode=ParseMode.MARKDOWN)

async def cmd_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("🔸 Пробный — 1000₽ (10 шт)", callback_data="buy_trial")],
          [InlineKeyboardButton("⭐ Навсегда — 2000₽ (безлимит)", callback_data="buy_full")]]
    await update.message.reply_text("💳 *Выбери тариф:*\n\nПосле оплаты пришли скриншот — активирую.", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def cb_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = q.from_user
    plan_name = "Пробный (1000₽)" if q.data == "buy_trial" else "Навсегда (2000₽)"
    await q.edit_message_text(f"✅ Тариф: *{plan_name}*\n\nПереведи оплату и пришли скриншот.\nТвой ID: `{user.id}`", parse_mode=ParseMode.MARKDOWN)
    if ADMIN_ID:
        try:
            await context.bot.send_message(ADMIN_ID, f"💰 Запрос!\n👤 @{user.username or '-'} (ID: {user.id})\n📦 {plan_name}\n\n/activate {user.id} trial\n/activate {user.id} full")
        except: pass

async def cmd_activate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Нет доступа"); return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Использование: /activate USER_ID trial|full [кол-во]"); return
    try:
        uid = int(args[0]); plan = args[1]; uses = int(args[2]) if len(args) > 2 else 10
        if plan == "full":
            authorized_users[uid] = {"plan": "full", "uses_left": 999999}
            await update.message.reply_text(f"✅ {uid}: безлимит")
            try: await context.bot.send_message(uid, "✅ Безлимитный доступ активирован! Пришли файл 🎉")
            except: pass
        elif plan == "trial":
            authorized_users[uid] = {"plan": "trial", "uses_left": uses}
            await update.message.reply_text(f"✅ {uid}: {uses} транскрипций")
            try: await context.bot.send_message(uid, f"✅ {uses} транскрипций активировано! Пришли файл 🎉")
            except: pass
    except ValueError:
        await update.message.reply_text("❌ Пример: /activate 123456789 trial 10")

async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if not authorized_users:
        await update.message.reply_text("Нет пользователей"); return
    lines = ["👥 Пользователи:"] + [f"• {uid}: {i['plan']}, {i['uses_left']} ост." for uid, i in authorized_users.items()]
    await update.message.reply_text("\n".join(lines))

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user; uid = user.id
    if uid not in authorized_users and uid != ADMIN_ID:
        authorized_users[uid] = {"plan": "free", "uses_left": FREE_USES}
    if not is_authorized(uid):
        await update.message.reply_text("❌ Транскрипции закончились.\n\nКупи доступ: /buy"); return

    file_obj = None; filename = "audio.mp3"
    if update.message.video: file_obj = update.message.video; filename = "video.mp4"
    elif update.message.audio: file_obj = update.message.audio; filename = update.message.audio.file_name or "audio.mp3"
    elif update.message.document: file_obj = update.message.document; filename = update.message.document.file_name or "file"
    elif update.message.voice: file_obj = update.message.voice; filename = "voice.ogg"
    elif update.message.video_note: file_obj = update.message.video_note; filename = "videonote.mp4"

    if not file_obj:
        await update.message.reply_text("Пришли видео или аудио файл 🎬"); return

    size_mb = round(file_obj.file_size / 1024 / 1024, 1)
    if file_obj.file_size > 20 * 1024 * 1024:
        await update.message.reply_text(f"⚠️ Файл {size_mb} МБ — лимит 20 МБ.\nДля больших файлов используй HTML транскрибатор."); return

    status_msg = await update.message.reply_text(f"⏳ Обрабатываю {size_mb} МБ... (1-3 мин)")
    ext = "." + filename.rsplit(".", 1)[-1] if "." in filename else ".tmp"
    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False); tmp_path = tmp.name; tmp.close()

    try:
        await status_msg.edit_text(f"⬇️ Скачиваю ({size_mb} МБ)...")
        tg_file = await file_obj.get_file()
        await tg_file.download_to_drive(tmp_path)
        await status_msg.edit_text("🤖 Транскрибирую через Gemini...")
        text = await transcribe_gemini(tmp_path, filename)
        consume_use(uid)
        uses = get_uses_left(uid)
        await status_msg.delete()
        if len(text) <= 3800:
            await update.message.reply_text(f"📝 *Готово!*\n\n{text}\n\n_{len(text)} символов · осталось: {uses}_", parse_mode=ParseMode.MARKDOWN)
        else:
            tf = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
            tf.write(text); tf_path = tf.name; tf.close()
            await update.message.reply_document(open(tf_path, "rb"), filename="транскрипция.txt", caption=f"📝 Готово! {len(text)} символов · осталось: {uses}")
            os.unlink(tf_path)
        if ADMIN_ID and uid != ADMIN_ID:
            try: await context.bot.send_message(ADMIN_ID, f"📊 Транскрипция\n👤 @{user.username or '-'} ({uid})\n📁 {filename} ({size_mb} МБ) → {len(text)} символов")
            except: pass
    except Exception as e:
        logger.error(f"Error {uid}: {e}")
        await status_msg.edit_text(f"❌ Ошибка: {str(e)[:300]}\n\nПопробуй ещё раз.")
    finally:
        try: os.unlink(tmp_path)
        except: pass

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("buy", cmd_buy))
    app.add_handler(CommandHandler("activate", cmd_activate))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CallbackQueryHandler(cb_buy, pattern="^buy_"))
    app.add_handler(MessageHandler(filters.VIDEO | filters.AUDIO | filters.Document.ALL | filters.VOICE | filters.VIDEO_NOTE, handle_file))
    logger.info("Bot started!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
