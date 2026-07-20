#!/usr/bin/env python3
"""
MediaBot — Bot de Telegram para descarga de contenido multimedia
Versión corregida - Descargas MP3/MP4 funcionando
"""
import os, re, logging, sqlite3, asyncio
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

# ══════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════
BOT_TOKEN    = os.getenv("BOT_TOKEN", "8723514629:AAFKeg8OGhMtFHRgPOHI4Qpw8c10riegDe0")
ADMIN_IDS    = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "1845182783").split(",") if x.strip().isdigit()]
DATABASE_URL = os.getenv("DATABASE_URL", "bot.db")
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/tmp/tgbot_dl")
MAX_MB       = 50

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

PLANS = {
    "free": {
        "name": "🆓 Free", "price_stars": 0,
        "daily_limit": 3, "max_quality": "720",
        "formats": ["mp3", "mp4"], "duration_days": 0,
    },
    "basic": {
        "name": "⭐ Basic", "price_stars": 150,
        "daily_limit": 30, "max_quality": "1080",
        "formats": ["mp3", "mp4", "m4a", "webm"], "duration_days": 30,
    },
    "pro": {
        "name": "💎 Pro", "price_stars": 350,
        "daily_limit": 100, "max_quality": "2160",
        "formats": ["mp3", "mp4", "m4a", "flac", "wav"], "duration_days": 30,
    },
    "elite": {
        "name": "👑 Elite", "price_stars": 750,
        "daily_limit": -1, "max_quality": "2160",
        "formats": ["mp3", "mp4", "m4a", "flac", "wav", "opus"], "duration_days": 30,
    },
}

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════
#  BASE DE DATOS (sin cambios, igual que tu código original)
# ══════════════════════════════════════════════════════════════════
def get_conn():
    conn = sqlite3.connect(DATABASE_URL, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id         INTEGER PRIMARY KEY,
                username        TEXT DEFAULT '',
                full_name       TEXT DEFAULT '',
                plan            TEXT DEFAULT 'free',
                plan_expires    TEXT DEFAULT '',
                downloads_today INTEGER DEFAULT 0,
                downloads_total INTEGER DEFAULT 0,
                last_reset      TEXT DEFAULT '',
                joined_at       TEXT DEFAULT CURRENT_TIMESTAMP,
                banned          INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS downloads (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER,
                url        TEXT,
                platform   TEXT,
                format     TEXT,
                status     TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS payments (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER,
                plan       TEXT,
                stars      INTEGER,
                charge_id  TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Migración segura: agrega columnas si no existen en DB antigua
        for col, definition in [
            ("last_reset",      "TEXT DEFAULT ''"),
            ("plan_expires",    "TEXT DEFAULT ''"),
            ("downloads_today", "INTEGER DEFAULT 0"),
            ("downloads_total", "INTEGER DEFAULT 0"),
            ("banned",          "INTEGER DEFAULT 0"),
        ]:
            try:
                c.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
            except Exception:
                pass
    log.info("✅ DB lista")

def get_user(user_id):
    with get_conn() as c:
        row = c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    return dict(row) if row else None

def get_or_create_user(user_id, username="", full_name=""):
    with get_conn() as c:
        row = c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            c.execute(
                "INSERT INTO users (user_id, username, full_name) VALUES (?,?,?)",
                (user_id, username, full_name)
            )
            row = c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    return dict(row)

def reset_daily(user_id):
    today = str(date.today())
    with get_conn() as c:
        row = c.execute("SELECT last_reset FROM users WHERE user_id=?", (user_id,)).fetchone()
        if row and row["last_reset"] != today:
            c.execute(
                "UPDATE users SET downloads_today=0, last_reset=? WHERE user_id=?",
                (today, user_id)
            )

def can_download(user_id):
    reset_daily(user_id)
    u = get_user(user_id)
    if not u:
        return False, "Usuario no encontrado."
    if u["banned"]:
        return False, "Tu cuenta está suspendida. 🚫"
    if u["plan"] != "free" and u["plan_expires"] and u["plan_expires"] < str(date.today()):
        set_plan(user_id, "free", "")
        u["plan"] = "free"
    lim = PLANS[u["plan"]]["daily_limit"]
    if lim == -1:
        return True, ""
    if u["downloads_today"] >= lim:
        return False, f"Límite diario alcanzado ({lim}/día). Mejora tu plan con /planes 💎"
    return True, ""

def inc_downloads(user_id):
    with get_conn() as c:
        c.execute(
            "UPDATE users SET downloads_today=downloads_today+1, downloads_total=downloads_total+1 WHERE user_id=?",
            (user_id,)
        )

def set_plan(user_id, plan, expires):
    with get_conn() as c:
        c.execute("UPDATE users SET plan=?, plan_expires=? WHERE user_id=?", (plan, expires, user_id))

def ban_user(user_id, val=1):
    with get_conn() as c:
        c.execute("UPDATE users SET banned=? WHERE user_id=?", (val, user_id))

def log_dl(user_id, url, platform, fmt, status):
    with get_conn() as c:
        c.execute(
            "INSERT INTO downloads (user_id,url,platform,format,status) VALUES (?,?,?,?,?)",
            (user_id, url, platform, fmt, status)
        )

def log_pay(user_id, plan, stars, charge_id):
    with get_conn() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(payments)").fetchall()}
        stars_col  = "stars" if "stars" in cols else "stars_amount"
        charge_col = "charge_id" if "charge_id" in cols else "telegram_charge_id"
        c.execute(
            f"INSERT INTO payments (user_id,plan,{stars_col},{charge_col}) VALUES (?,?,?,?)",
            (user_id, plan, stars, charge_id)
        )

def get_stats():
    with get_conn() as c:
        dl_cols  = {r[1] for r in c.execute("PRAGMA table_info(downloads)").fetchall()}
        pay_cols = {r[1] for r in c.execute("PRAGMA table_info(payments)").fetchall()}

        # Compatibilidad con esquemas antiguos/nuevos
        status_filter = "status='success'" if "status" in dl_cols else "1=1"
        stars_col     = "stars" if "stars" in pay_cols else "stars_amount"

        total = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        paid  = c.execute("SELECT COUNT(*) FROM users WHERE plan!='free'").fetchone()[0]
        dls   = c.execute(f"SELECT COUNT(*) FROM downloads WHERE {status_filter}").fetchone()[0]
        if "status" in dl_cols:
            dls_legacy = c.execute("SELECT COUNT(*) FROM downloads WHERE status='ok'").fetchone()[0]
            dls += dls_legacy
        stars = c.execute(f"SELECT COALESCE(SUM({stars_col}),0) FROM payments").fetchone()[0]
        new_w = c.execute("SELECT COUNT(*) FROM users WHERE joined_at>=date('now','-7 days')").fetchone()[0]
    return {"total": total, "paid": paid, "dls": dls, "stars": stars, "new_w": new_w}

def all_users():
    with get_conn() as c:
        return [r[0] for r in c.execute("SELECT user_id FROM users WHERE banned=0").fetchall()]

def cleanup(user_id):
    """Elimina archivos temporales del usuario"""
    for f in os.listdir(DOWNLOAD_DIR):
        if f.startswith(str(user_id)):
            try:
                os.remove(os.path.join(DOWNLOAD_DIR, f))
            except:
                pass

# ══════════════════════════════════════════════════════════════════
#  DOWNLOADER - VERSIÓN CORREGIDA
# ══════════════════════════════════════════════════════════════════
SUPPORTED = [
    "youtube.com", "youtu.be", "tiktok.com", "instagram.com",
    "twitter.com", "x.com", "soundcloud.com", "facebook.com",
    "fb.watch", "twitch.tv", "vimeo.com", "reddit.com", "dailymotion.com",
]

def detect_platform(url):
    u = url.lower()
    for platform, domains in [
        ("youtube",    ["youtube.com", "youtu.be"]),
        ("tiktok",     ["tiktok.com"]),
        ("instagram",  ["instagram.com"]),
        ("twitter",    ["twitter.com", "x.com"]),
        ("soundcloud", ["soundcloud.com"]),
        ("facebook",   ["facebook.com", "fb.watch"]),
        ("twitch",     ["twitch.tv"]),
        ("vimeo",      ["vimeo.com"]),
        ("reddit",     ["reddit.com"]),
    ]:
        if any(d in u for d in domains):
            return platform
    return "other"

def is_supported(url):
    return any(d in url.lower() for d in SUPPORTED)

def extract_url(text):
    m = re.search(r'https?://[^\s]+', text)
    return m.group(0) if m else None

def fmt_dur(s):
    if not s: return "?"
    try:
        m, s = divmod(int(s), 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
    except:
        return "?"

def _tiktok_opts():
    return {"extractor_args": {"tiktok": {"api_hostname": "api22-normal-c-useast2a.tiktokv.com"}}}

def get_info(url):
    try:
        import yt_dlp
        opts = {
            "quiet": True, 
            "no_warnings": True, 
            "skip_download": True,
            "extract_flat": True
        }
        if "tiktok" in url:
            opts.update(_tiktok_opts())
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(url, download=False)
            return {
                "title":    info.get("title", "?"),
                "duration": info.get("duration", 0),
                "uploader": info.get("uploader", info.get("channel", "")),
            }
    except Exception as e:
        log.error(f"Error getting info: {e}")
        return None

async def dl_audio(url, user_id):
    """Versión corregida para descargar MP3"""
    try:
        import yt_dlp
        # Nombre de archivo temporal sin extensión fija
        temp_template = os.path.join(DOWNLOAD_DIR, f"{user_id}_audio_%(title)s.%(ext)s")
        
        # Opciones para audio MP3
        opts = {
            'format': 'bestaudio/best',
            'outtmpl': temp_template,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
        }

        # Opciones específicas para TikTok
        if "tiktok" in url:
            opts.update({
                'extractor_args': {
                    'tiktok': {
                        'api_hostname': 'api22-normal-c-useast2a.tiktokv.com',
                        'app_name': 'musical_ly',
                    }
                }
            })

        loop = asyncio.get_event_loop()
        
        # Ejecutar la descarga en un hilo separado
        def download():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=True)
        
        info = await loop.run_in_executor(None, download)
        
        # Buscar el archivo MP3 generado (puede tener cualquier nombre)
        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(str(user_id)) and f.endswith('.mp3'):
                path = os.path.join(DOWNLOAD_DIR, f)
                mb = os.path.getsize(path) / (1024 * 1024)
                
                if mb > MAX_MB:
                    os.remove(path)
                    return {"ok": False, "err": f"Archivo demasiado grande ({mb:.1f}MB > {MAX_MB}MB)"}
                
                return {
                    "ok": True,
                    "path": path,
                    "title": info.get('title', 'Audio'),
                    "mb": round(mb, 2),
                    "duration": info.get('duration', 0)
                }
        
        return {"ok": False, "err": "No se pudo generar el archivo MP3"}
        
    except Exception as e:
        log.error(f"Error en dl_audio: {e}")
        return {"ok": False, "err": str(e)[:200]}

async def dl_video(url, user_id, quality="720"):
    """Versión corregida para descargar MP4"""
    try:
        import yt_dlp
        # Template para el archivo de salida
        out_template = os.path.join(DOWNLOAD_DIR, f"{user_id}_video_%(title)s.%(ext)s")
        
        # Mapa de calidad a formato
        quality_map = {
            "360": "best[height<=360]",
            "720": "best[height<=720]",
            "1080": "best[height<=1080]",
            "2160": "best[height<=2160]",
        }
        
        format_spec = quality_map.get(quality, "best[height<=720]")
        
        # Opciones para video
        opts = {
            'format': format_spec,
            'outtmpl': out_template,
            'merge_output_format': 'mp4',
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
        }

        # Opciones específicas para TikTok (sin marca de agua)
        if "tiktok" in url:
            opts.update({
                'format': 'bestvideo+bestaudio/best',
                'extractor_args': {
                    'tiktok': {
                        'api_hostname': 'api22-normal-c-useast2a.tiktokv.com',
                        'app_name': 'musical_ly',
                    }
                }
            })

        loop = asyncio.get_event_loop()
        
        def download():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=True)
        
        info = await loop.run_in_executor(None, download)
        
        # Buscar el archivo MP4 generado
        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(str(user_id)) and f.endswith('.mp4'):
                path = os.path.join(DOWNLOAD_DIR, f)
                mb = os.path.getsize(path) / (1024 * 1024)
                
                if mb > MAX_MB:
                    os.remove(path)
                    return {"ok": False, "err": f"Archivo demasiado grande ({mb:.1f}MB > {MAX_MB}MB)"}
                
                return {
                    "ok": True,
                    "path": path,
                    "title": info.get('title', 'Video'),
                    "mb": round(mb, 2),
                    "duration": info.get('duration', 0)
                }
        
        return {"ok": False, "err": "No se pudo generar el archivo MP4"}
        
    except Exception as e:
        log.error(f"Error en dl_video: {e}")
        return {"ok": False, "err": str(e)[:200]}

# ══════════════════════════════════════════════════════════════════
#  TELEGRAM IMPORTS
# ══════════════════════════════════════════════════════════════════
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, PreCheckoutQueryHandler, filters,
)
from telegram.constants import ChatAction

# ══════════════════════════════════════════════════════════════════
#  TECLADOS (igual que tu código original)
# ══════════════════════════════════════════════════════════════════
def kb_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 Descargar",        callback_data="menu_dl"),
         InlineKeyboardButton("🔄 Convertir",        callback_data="menu_conv")],
        [InlineKeyboardButton("💎 Planes & Precios", callback_data="menu_plans"),
         InlineKeyboardButton("📊 Mi Cuenta",        callback_data="menu_account")],
        [InlineKeyboardButton("❓ Ayuda",            callback_data="menu_help")],
    ])

def kb_download():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 MP3 (Audio)",      callback_data="fmt_mp3"),
         InlineKeyboardButton("🎬 MP4 360p",         callback_data="fmt_mp4_360")],
        [InlineKeyboardButton("🎬 MP4 720p HD",      callback_data="fmt_mp4_720"),
         InlineKeyboardButton("🎬 MP4 1080p",        callback_data="fmt_mp4_1080")],
        [InlineKeyboardButton("🎬 MP4 4K 🔥",        callback_data="fmt_mp4_2160"),
         InlineKeyboardButton("🚫 TikTok sin marca", callback_data="fmt_tiktok")],
        [InlineKeyboardButton("❌ Cancelar",          callback_data="cancel")],
    ])

def kb_back():
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Volver al Menú", callback_data="menu_home")]])

# ══════════════════════════════════════════════════════════════════
#  HELPERS (igual que tu código original)
# ══════════════════════════════════════════════════════════════════
async def show_home(q):
    u   = q.from_user
    dbu = get_or_create_user(u.id, u.username or "", u.full_name or "")
    reset_daily(u.id)
    dbu = get_user(u.id)
    p   = PLANS[dbu["plan"]]
    lim = "∞" if p["daily_limit"] == -1 else str(p["daily_limit"])
    await q.edit_message_text(
        f"👋 ¡Bienvenido a <b>MediaBot</b>!\n\n"
        f"🎵 Descarga música · 🎬 Videos · 🚫 TikTok sin marca\n"
        f"🔄 Convierte formatos · 💳 Planes con Telegram Stars\n\n"
        f"📦 Plan: <b>{p['name']}</b>  |  📥 Hoy: <b>{dbu['downloads_today']}/{lim}</b>\n\n"
        f"Pega un enlace o usa el menú 👇",
        parse_mode="HTML", reply_markup=kb_main()
    )

async def show_planes(q):
    u   = q.from_user
    dbu = get_user(u.id) or get_or_create_user(u.id, u.username or "", u.full_name or "")
    cur = dbu["plan"]
    text = "💎 <b>Planes de Suscripción</b>\n\n"
    rows = []
    for pid, p in PLANS.items():
        if pid == "free":
            continue
        is_cur = pid == cur
        mark   = "✅ <b>PLAN ACTUAL</b>\n" if is_cur else ""
        lim    = "∞ ilimitadas" if p["daily_limit"] == -1 else f"{p['daily_limit']}/día"
        text  += f"{mark}<b>{p['name']}</b> — ⭐ {p['price_stars']} Stars/mes\n"
        text  += f"  📥 {lim} · 🎬 {p['max_quality']}p · {', '.join(p['formats'][:3])}\n\n"
        if not is_cur:
            rows.append([InlineKeyboardButton(
                f"Contratar {p['name']} — ⭐ {p['price_stars']} Stars",
                callback_data=f"buy_{pid}"
            )])
    text += "ℹ️ Pagos seguros con <b>Telegram Stars</b>. Duración: 30 días."
    rows.append([InlineKeyboardButton("◀️ Volver", callback_data="menu_home")])
    await q.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))

async def show_cuenta(q):
    u   = q.from_user
    get_or_create_user(u.id, u.username or "", u.full_name or "")
    reset_daily(u.id)
    dbu = get_user(u.id)
    p   = PLANS[dbu["plan"]]
    lim = "∞" if p["daily_limit"] == -1 else str(p["daily_limit"])
    await q.edit_message_text(
        f"👤 <b>Mi Cuenta</b>\n\n"
        f"🆔 ID: <code>{dbu['user_id']}</code>\n"
        f"👤 Nombre: {dbu['full_name'] or 'N/A'}\n"
        f"📦 Plan: <b>{p['name']}</b>\n"
        f"📅 Vence: {dbu['plan_expires'] or 'N/A'}\n\n"
        f"📥 Hoy: <b>{dbu['downloads_today']}/{lim}</b>\n"
        f"📥 Total: <b>{dbu['downloads_total']}</b>\n"
        f"📅 Miembro desde: {str(dbu['joined_at'])[:10]}\n\n"
        f"🎬 Calidad máx: <b>{p['max_quality']}p</b>\n"
        f"🎨 Formatos: {', '.join(p['formats'])}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💎 Mejorar Plan", callback_data="menu_plans")],
            [InlineKeyboardButton("◀️ Volver",       callback_data="menu_home")],
        ])
    )

# ══════════════════════════════════════════════════════════════════
#  COMANDOS (igual que tu código original)
# ══════════════════════════════════════════════════════════════════
async def cmd_start(update: Update, ctx):
    u   = update.effective_user
    dbu = get_or_create_user(u.id, u.username or "", u.full_name or "")
    reset_daily(u.id)
    dbu = get_user(u.id)
    p   = PLANS[dbu["plan"]]
    lim = "∞" if p["daily_limit"] == -1 else str(p["daily_limit"])
    await update.message.reply_html(
        f"👋 ¡Bienvenido a <b>MediaBot</b>!\n\n"
        f"🎵 Descarga música · 🎬 Videos · 🚫 TikTok sin marca\n"
        f"🔄 Convierte formatos · 💳 Planes con Telegram Stars\n\n"
        f"📦 Plan: <b>{p['name']}</b>  |  📥 Hoy: <b>{dbu['downloads_today']}/{lim}</b>\n\n"
        f"Pega un enlace o usa el menú 👇",
        reply_markup=kb_main()
    )

async def cmd_planes(update: Update, ctx):
    u   = update.effective_user
    dbu = get_user(u.id) or get_or_create_user(u.id, u.username or "", u.full_name or "")
    cur = dbu["plan"]
    text = "💎 <b>Planes de Suscripción</b>\n\n"
    rows = []
    for pid, p in PLANS.items():
        if pid == "free":
            continue
        is_cur = pid == cur
        mark   = "✅ <b>PLAN ACTUAL</b>\n" if is_cur else ""
        lim    = "∞ ilimitadas" if p["daily_limit"] == -1 else f"{p['daily_limit']}/día"
        text  += f"{mark}<b>{p['name']}</b> — ⭐ {p['price_stars']} Stars/mes\n"
        text  += f"  📥 {lim} · 🎬 {p['max_quality']}p · {', '.join(p['formats'][:3])}\n\n"
        if not is_cur:
            rows.append([InlineKeyboardButton(
                f"Contratar {p['name']} — ⭐ {p['price_stars']} Stars",
                callback_data=f"buy_{pid}"
            )])
    text += "ℹ️ Pagos seguros con <b>Telegram Stars</b>. Duración: 30 días."
    rows.append([InlineKeyboardButton("◀️ Volver", callback_data="menu_home")])
    await update.message.reply_html(text, reply_markup=InlineKeyboardMarkup(rows))

async def cmd_cuenta(update: Update, ctx):
    u   = update.effective_user
    get_or_create_user(u.id, u.username or "", u.full_name or "")
    reset_daily(u.id)
    dbu = get_user(u.id)
    p   = PLANS[dbu["plan"]]
    lim = "∞" if p["daily_limit"] == -1 else str(p["daily_limit"])
    await update.message.reply_html(
        f"👤 <b>Mi Cuenta</b>\n\n"
        f"🆔 ID: <code>{dbu['user_id']}</code>\n"
        f"👤 Nombre: {dbu['full_name'] or 'N/A'}\n"
        f"📦 Plan: <b>{p['name']}</b>\n"
        f"📅 Vence: {dbu['plan_expires'] or 'N/A'}\n\n"
        f"📥 Hoy: <b>{dbu['downloads_today']}/{lim}</b>\n"
        f"📥 Total: <b>{dbu['downloads_total']}</b>\n"
        f"📅 Miembro desde: {str(dbu['joined_at'])[:10]}\n\n"
        f"🎬 Calidad máx: <b>{p['max_quality']}p</b>\n"
        f"🎨 Formatos: {', '.join(p['formats'])}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💎 Mejorar Plan", callback_data="menu_plans")],
            [InlineKeyboardButton("◀️ Volver",       callback_data="menu_home")],
        ])
    )

async def cmd_ayuda(update: Update, ctx):
    await update.message.reply_html(
        "❓ <b>¿Cómo usar MediaBot?</b>\n\n"
        "Simplemente <b>pega un enlace</b> y te pregunto el formato.\n\n"
        "<b>✅ Plataformas:</b>\n"
        "▶️ YouTube · 🎵 TikTok (sin marca) · 📸 Instagram\n"
        "🐦 Twitter/X · 🎧 SoundCloud · 👥 Facebook\n"
        "🎮 Twitch · 🎞️ Vimeo · 🤖 Reddit · y más...\n\n"
        "<b>📋 Comandos:</b>\n"
        "/start · /planes · /cuenta · /ayuda\n\n"
        "<b>⚠️ Límite:</b> 50MB por archivo.",
        reply_markup=kb_back()
    )

# ══════════════════════════════════════════════════════════════════
#  BOTONES INLINE — Handler principal (CORREGIDO)
# ══════════════════════════════════════════════════════════════════
async def on_button(update: Update, ctx):
    q = update.callback_query
    data = q.data
    uid = q.from_user.id
    await q.answer()

    # --- NAVEGACIÓN MENÚS ---
    if data == "menu_dl":
        await q.edit_message_text(
            "📥 <b>¿Qué quieres descargar?</b>\n\nSimplemente pega el enlace (YouTube, TikTok, Instagram, etc.) directamente en este chat y yo detectaré el formato.",
            parse_mode="HTML", reply_markup=kb_back()
        )

    elif data == "menu_conv":
        await q.edit_message_text(
            "🔄 <b>Conversor de Formatos</b>\n\nPara convertir:\n1. Envíame un archivo de audio o video.\n2. Responde a ese archivo con el comando:\n<code>/convertir mp3</code> o <code>/convertir mp4</code>",
            parse_mode="HTML", reply_markup=kb_back()
        )

    elif data == "menu_account":
        await show_cuenta(q)

    elif data == "menu_plans":
        await show_planes(q)

    elif data == "menu_help":
        # Necesitamos crear un update falso para cmd_ayuda
        update.callback_query = q
        await cmd_ayuda(update, ctx)

    elif data == "menu_home":
        dbu = get_or_create_user(uid, q.from_user.username or "", q.from_user.full_name or "")
        reset_daily(uid)
        p = PLANS[dbu["plan"]]
        lim = "∞" if p["daily_limit"] == -1 else str(p["daily_limit"])
        text = (
            f"👋 ¡Bienvenido a <b>MediaBot</b>!\n\n"
            f"📦 Plan: <b>{p['name']}</b> | 📥 Hoy: <b>{dbu['downloads_today']}/{lim}</b>\n\n"
            f"Pega un enlace o usa el menú 👇"
        )
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb_main())

    elif data == "cancel":
        ctx.user_data.pop("pending_url", None)
        await q.edit_message_text("❌ Operación cancelada.", reply_markup=kb_back())

    # --- LÓGICA DE COMPRA ---
    elif data.startswith("buy_"):
        plan_id = data.replace("buy_", "")
        p = PLANS.get(plan_id)
        if not p: return
        try:
            await ctx.bot.send_invoice(
                chat_id=uid, 
                title=f"Plan {p['name']}", 
                description=f"Mejora tu límite a {p['daily_limit']} descargas diarias",
                payload=f"plan_{plan_id}_{uid}", 
                provider_token="", 
                currency="XTR", 
                prices=[LabeledPrice(p["name"], p["price_stars"])]
            )
        except Exception as e:
            await q.edit_message_text(f"❌ Error al generar factura: {e}")

    # --- LÓGICA DE DESCARGA (CORREGIDA) ---
    elif data.startswith("fmt_"):
        url = ctx.user_data.get("pending_url")
        if not url:
            await q.edit_message_text("❌ No hay enlace pendiente. Envía el link de nuevo.")
            return

        # 1. Info del usuario y validación de límites diarios
        dbu = get_or_create_user(uid, q.from_user.username or "", q.from_user.full_name or "")
        can, reason = can_download(uid)
        if not can:
            await q.edit_message_text(f"⛔ {reason}", reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💎 Ver Planes", callback_data="menu_plans")
            ]]))
            return

        # 2. Determinar formato y calidad pedida
        is_video = data != "fmt_mp3"
        
        # Extraemos la calidad numérica
        if data == "fmt_mp3":
            q_asked = "mp3"
            q_int = 0
        elif data == "fmt_tiktok":
            q_asked = "720"
            q_int = 720
        else:
            # fmt_mp4_360, fmt_mp4_720, etc.
            q_asked = data.replace("fmt_mp4_", "")
            q_int = int(q_asked) if q_asked.isdigit() else 720
        
        # 3. Validar restricción Premium según el Plan
        p_config = PLANS[dbu["plan"]]
        max_allowed = p_config["max_quality"]
        
        if is_video and q_int > int(max_allowed):
            await q.edit_message_text(
                f"⭐ <b>Calidad Premium</b>\n\nTu plan actual ({p_config['name']}) solo permite hasta <b>{max_allowed}p</b>.\n"
                f"Para descargar en <b>{q_int}p</b> o <b>4K</b>, mejora tu plan 👇",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("💎 Ver Planes", callback_data="menu_plans"),
                    InlineKeyboardButton("🔙 Volver", callback_data="menu_home")
                ]])
            )
            return

        # 4. Iniciar proceso de descarga
        fmt_name = "MP3" if not is_video else f"MP4 {q_int}p"
        await q.edit_message_text(f"⏳ Descargando <b>{fmt_name}</b>...\nEsto puede tardar un momento ⏱", parse_mode="HTML")

        try:
            if is_video:
                result = await dl_video(url, uid, str(q_int))
            else:
                result = await dl_audio(url, uid)

            if not result["ok"]:
                await q.edit_message_text(f"❌ <b>Error:</b>\n<code>{result['err']}</code>", parse_mode="HTML")
                return

            # 5. Enviar el archivo descargado
            path = result["path"]
            title = result.get("title", "Archivo")
            mb = result.get("mb", 0)
            dur = result.get("duration", 0)
            platform = detect_platform(url)
            
            # Limitar título a 60 caracteres
            short_title = title[:57] + "..." if len(title) > 60 else title
            cap = f"✅ <b>{short_title}</b>\n📦 {mb}MB · ⏱ {fmt_dur(dur)} · 📱 {platform.capitalize()}"

            # Enviar archivo
            with open(path, 'rb') as f:
                if is_video:
                    await ctx.bot.send_video(
                        chat_id=q.message.chat_id,
                        video=f,
                        caption=cap,
                        parse_mode="HTML",
                        supports_streaming=True,
                        read_timeout=60,
                        write_timeout=60
                    )
                else:
                    await ctx.bot.send_audio(
                        chat_id=q.message.chat_id,
                        audio=f,
                        caption=cap,
                        parse_mode="HTML",
                        title=title,
                        performer=result.get('uploader', ''),
                        read_timeout=60,
                        write_timeout=60
                    )
            
            # Actualizar contadores y limpiar
            inc_downloads(uid)
            log_dl(uid, url, platform, fmt_name, "ok")
            await q.edit_message_text("✅ ¡Listo! Archivo enviado arriba 👆")
            
        except Exception as e:
            log.error(f"Error en descarga: {e}")
            await q.edit_message_text(f"❌ Error al procesar: <code>{str(e)[:100]}</code>", parse_mode="HTML")
        finally:
            # Limpiar archivos temporales
            cleanup(uid)
            ctx.user_data.pop("pending_url", None)

# ══════════════════════════════════════════════════════════════════
#  MENSAJES — Detectar URLs automáticamente
# ══════════════════════════════════════════════════════════════════
async def on_message(update: Update, ctx):
    if not update.message or not update.message.text:
        return
        
    text = update.message.text
    url = extract_url(text)
    
    if not url or not is_supported(url):
        return

    uid = update.effective_user.id
    get_or_create_user(uid, update.effective_user.username or "", update.effective_user.full_name or "")

    can, reason = can_download(uid)
    if not can:
        await update.message.reply_html(
            f"⛔ {reason}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💎 Ver Planes", callback_data="menu_plans")
            ]])
        )
        return

    # Guardar URL para cuando el usuario elija formato
    ctx.user_data["pending_url"] = url
    
    platform = detect_platform(url)
    icons = {
        "youtube": "▶️", "tiktok": "🎵", "instagram": "📸",
        "twitter": "🐦", "soundcloud": "🎧", "facebook": "👥",
        "twitch": "🎮", "vimeo": "🎞️", "other": "🌐",
    }
    icon = icons.get(platform, "🌐")
    
    await update.message.reply_chat_action(ChatAction.TYPING)
    
    # Obtener información del video (opcional, puede fallar)
    info = get_info(url)
    
    if info:
        caption = (
            f"{icon} <b>{info['title'][:60]}</b>\n"
            f"⏱ {fmt_dur(info['duration'])} · 👤 {info['uploader']}\n\n"
            f"¿En qué formato lo descargo?"
        )
    else:
        caption = f"{icon} Enlace de <b>{platform.capitalize()}</b> detectado. ¿Qué formato quieres?"

    await update.message.reply_html(caption, reply_markup=kb_download())

# ══════════════════════════════════════════════════════════════════
#  CONVERSOR
# ══════════════════════════════════════════════════════════════════
async def cmd_convertir(update: Update, ctx):
    allowed = ["mp3", "mp4", "m4a", "ogg", "wav", "flac", "opus", "webm"]
    args    = ctx.args
    if not args or args[0].lower() not in allowed:
        await update.message.reply_html(
            f"🔄 <b>Conversor</b>\n\nResponde a un archivo con:\n"
            f"<code>/convertir [formato]</code>\n\n"
            f"<b>Formatos:</b> {', '.join(allowed)}"
        )
        return
    reply = update.message.reply_to_message
    if not reply or not (reply.audio or reply.video or reply.document):
        await update.message.reply_html("❌ Responde a un archivo de audio/video con este comando.")
        return
    can, reason = can_download(update.effective_user.id)
    if not can:
        await update.message.reply_html(f"⛔ {reason}")
        return

    target = args[0].lower()
    fobj   = reply.audio or reply.video or reply.document
    msg    = await update.message.reply_html("⏳ Descargando archivo...")
    tgfile = await ctx.bot.get_file(fobj.file_id)
    ext    = "mp4" if reply.video else "mp3" if reply.audio else "bin"
    inp    = os.path.join(DOWNLOAD_DIR, f"{update.effective_user.id}_conv.{ext}")
    await tgfile.download_to_drive(inp)

    await msg.edit_text(f"🔄 Convirtiendo a {target.upper()}...", parse_mode="HTML")
    out = os.path.join(DOWNLOAD_DIR, f"{update.effective_user.id}_out.{target}")

    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", inp, out,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, err = await proc.communicate()
        if proc.returncode != 0 or not os.path.exists(out):
            await msg.edit_text(f"❌ Error FFmpeg:\n<code>{err.decode()[-200:]}</code>", parse_mode="HTML")
            return
        mb  = os.path.getsize(out) / 1048576
        cap = f"✅ Convertido a <b>{target.upper()}</b> · {mb:.1f}MB"
        
        with open(out, 'rb') as f:
            if target in ["mp4", "webm"]:
                await update.message.reply_video(f, caption=cap, parse_mode="HTML")
            else:
                await update.message.reply_audio(f, caption=cap, parse_mode="HTML")
        
        await msg.edit_text("✅ ¡Conversión completada!")
    except Exception as e:
        await msg.edit_text(f"❌ Error: {str(e)[:200]}")
    finally:
        cleanup(update.effective_user.id)

async def on_file(update: Update, ctx):
    await update.message.reply_html(
        "📁 Archivo recibido. Responde a este mensaje con:\n\n"
        "<code>/convertir mp3</code>\n"
        "<code>/convertir mp4</code>\n"
        "<code>/convertir flac</code>\n"
        "<code>/convertir wav</code>"
    )

# ══════════════════════════════════════════════════════════════════
#  PAGOS TELEGRAM STARS
# ══════════════════════════════════════════════════════════════════
async def pre_checkout(update: Update, ctx):
    q = update.pre_checkout_query
    if q.invoice_payload.startswith("plan_"):
        await q.answer(ok=True)
    else:
        await q.answer(ok=False, error_message="Pago no válido.")

async def on_payment(update: Update, ctx):
    pay     = update.message.successful_payment
    uid     = update.effective_user.id
    parts   = pay.invoice_payload.split("_")
    plan_id = parts[1] if len(parts) >= 2 else "basic"
    if plan_id not in PLANS:
        await update.message.reply_html("❌ Error al activar el plan.")
        return
    p       = PLANS[plan_id]
    expires = str(date.today() + timedelta(days=30))
    set_plan(uid, plan_id, expires)
    log_pay(uid, plan_id, pay.total_amount, pay.telegram_payment_charge_id)
    lim_str = "∞ ilimitadas" if p["daily_limit"] == -1 else f"{p['daily_limit']}/día"
    await update.message.reply_html(
        f"🎉 <b>¡Plan {p['name']} activado!</b>\n\n"
        f"✅ {lim_str}\n"
        f"🎬 Hasta {p['max_quality']}p\n"
        f"📅 Válido hasta: <b>{expires}</b>\n\n🚀",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🏠 Ir al Menú", callback_data="menu_home")
        ]])
    )

# ══════════════════════════════════════════════════════════════════
#  ADMIN (sin cambios)
# ══════════════════════════════════════════════════════════════════
def is_admin(uid):
    return uid in ADMIN_IDS

async def cmd_admin(update: Update, ctx):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ No autorizado."); return
    s = get_stats()
    await update.message.reply_html(
        f"🛡️ <b>Panel Admin</b>\n\n"
        f"👥 Usuarios: <b>{s['total']}</b> · 💎 De pago: <b>{s['paid']}</b>\n"
        f"📥 Descargas: <b>{s['dls']}</b> · ⭐ Stars: <b>{s['stars']}</b>\n"
        f"🆕 Esta semana: <b>{s['new_w']}</b>\n\n"
        f"<b>Comandos:</b>\n"
        f"/ban [id] · /unban [id]\n"
        f"/setplan [id] [plan]\n"
        f"/broadcast [msg]\n"
        f"/userinfo [id]\n\n"
        f"<b>Planes:</b> {', '.join(PLANS.keys())}"
    )

async def cmd_ban(update: Update, ctx):
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Uso: /ban [id]"); return
    try:
        ban_user(int(ctx.args[0]))
        await update.message.reply_html(f"✅ Baneado <code>{ctx.args[0]}</code>")
    except Exception: await update.message.reply_text("❌ ID inválido")

async def cmd_unban(update: Update, ctx):
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Uso: /unban [id]"); return
    try:
        ban_user(int(ctx.args[0]), 0)
        await update.message.reply_html(f"✅ Desbaneado <code>{ctx.args[0]}</code>")
    except Exception: await update.message.reply_text("❌ ID inválido")

async def cmd_setplan(update: Update, ctx):
    if not is_admin(update.effective_user.id): return
    if len(ctx.args) < 2:
        await update.message.reply_text(f"Uso: /setplan [id] [plan]\nPlanes: {', '.join(PLANS.keys())}"); return
    try:
        uid     = int(ctx.args[0])
        plan_id = ctx.args[1].lower()
        if plan_id not in PLANS:
            await update.message.reply_text("❌ Plan inválido"); return
        expires = str(date.today() + timedelta(days=30)) if plan_id != "free" else ""
        set_plan(uid, plan_id, expires)
        await update.message.reply_html(
            f"✅ Plan <b>{PLANS[plan_id]['name']}</b> asignado a <code>{uid}</code>\n📅 Vence: {expires or 'N/A'}"
        )
    except Exception: await update.message.reply_text("❌ ID inválido")

async def cmd_broadcast(update: Update, ctx):
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Uso: /broadcast [mensaje]"); return
    msg_text = " ".join(ctx.args)
    users  = all_users()
    status = await update.message.reply_text(f"📢 Enviando a {len(users)} usuarios...")
    sent = failed = 0
    for uid in users:
        try:
            await ctx.bot.send_message(uid, f"📢 <b>Anuncio:</b>\n\n{msg_text}", parse_mode="HTML")
            sent += 1
        except Exception: failed += 1
    await status.edit_text(f"✅ Enviados: {sent} · Fallidos: {failed}")

async def cmd_userinfo(update: Update, ctx):
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Uso: /userinfo [id]"); return
    try:
        u = get_user(int(ctx.args[0]))
        if not u: await update.message.reply_text("❌ No encontrado"); return
        p = PLANS.get(u["plan"], {})
        await update.message.reply_html(
            f"👤 <b>Usuario</b>\n"
            f"🆔 <code>{u['user_id']}</code>\n"
            f"👤 {u['full_name'] or 'N/A'} · @{u['username'] or 'N/A'}\n"
            f"📦 Plan: <b>{p.get('name', u['plan'])}</b>\n"
            f"📅 Vence: {u['plan_expires'] or 'N/A'}\n"
            f"📥 Total: {u['downloads_total']} · Hoy: {u['downloads_today']}\n"
            f"🚫 Baneado: {'Sí ⛔' if u['banned'] else 'No ✅'}\n"
            f"📅 Registro: {str(u['joined_at'])[:10]}"
        )
    except Exception: await update.message.reply_text("❌ ID inválido")

# ══════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    # Usuario
    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("menu",      cmd_start))
    app.add_handler(CommandHandler("planes",    cmd_planes))
    app.add_handler(CommandHandler("cuenta",    cmd_cuenta))
    app.add_handler(CommandHandler("ayuda",     cmd_ayuda))
    app.add_handler(CommandHandler("help",      cmd_ayuda))
    app.add_handler(CommandHandler("convertir", cmd_convertir))

    # Admin
    app.add_handler(CommandHandler("admin",     cmd_admin))
    app.add_handler(CommandHandler("ban",       cmd_ban))
    app.add_handler(CommandHandler("unban",     cmd_unban))
    app.add_handler(CommandHandler("setplan",   cmd_setplan))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("userinfo",  cmd_userinfo))

    # Botones
    app.add_handler(CallbackQueryHandler(on_button))

    # Pagos
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, on_payment))

    # Mensajes y archivos
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_handler(MessageHandler(filters.AUDIO | filters.VIDEO | filters.Document.ALL, on_file))

    log.info(f"🚀 Bot iniciado | Admins: {ADMIN_IDS}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
