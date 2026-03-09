#!/usr/bin/env python3
"""
🐻 GrizzlySMS Telegram Bot v3
- Setiap user input API Key GrizzlySMS mereka sendiri
- Whitelist Telegram ID (hanya yang terdaftar bisa akses)
- Default: Vietnam WhatsApp ~$0.19
"""

import logging
import requests
import time
import json
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler,
    MessageHandler, filters, ContextTypes
)

# ─── KONFIGURASI ────────────────────────────────────────────────────────────

import os
import asyncio

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8762689776:AAGmCnAH_WP6yhcH4EwpTPFxi8Ar0tW54IY")

# Daftar Telegram ID yang boleh akses bot
# Cek ID kamu di @userinfobot
# Kosongkan list [] = semua orang bisa akses (tidak disarankan)
ALLOWED_IDS = [
    7052770466,   # ← ganti dengan Telegram ID kamu
    # 987654321, # ← tambah ID orang lain di sini
]

API_BASE  = "https://grizzlysms.com/stubs/handler_api.php"
API_BASE2 = "https://api.grizzlysms.com/stubs/handler_api.php"

# Default layanan: Vietnam WhatsApp
DEFAULT_SERVICE  = "wa"
DEFAULT_COUNTRY  = "18"
DEFAULT_SVC_NAME = "WhatsApp"
DEFAULT_CTR_NAME = "🇻🇳 Vietnam"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Connection": "keep-alive",
}

SMS_POLL_INTERVAL = 5
SMS_MAX_WAIT      = 300

# Storage global untuk auto-polling
# {activation_id: {chat_id, api_key, phone, service, country, start_time}}
AUTO_POLL_JOBS: dict = {}

# ─── LOGGING ────────────────────────────────────────────────────────────────

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── WHITELIST CHECK ────────────────────────────────────────────────────────

def is_allowed(user_id: int) -> bool:
    if not ALLOWED_IDS:
        return True  # kalau list kosong = semua bisa akses
    return user_id in ALLOWED_IDS

async def check_access(update: Update) -> bool:
    """Cek apakah user boleh akses. Return True jika boleh."""
    user = update.effective_user
    if not is_allowed(user.id):
        await update.message.reply_text(
            "🚫 *Akses Ditolak*\n\n"
            f"ID Telegram kamu (`{user.id}`) tidak terdaftar.\n"
            "Hubungi admin untuk mendapatkan akses.",
            parse_mode="Markdown"
        )
        logger.warning(f"Akses ditolak untuk user: {user.id} (@{user.username})")
        return False
    return True

# ─── HELPER ─────────────────────────────────────────────────────────────────

def get_api_key(ctx) -> str | None:
    return ctx.user_data.get("api_key")

def add_log(ctx, msg: str):
    if "log" not in ctx.user_data:
        ctx.user_data["log"] = []
    ts = datetime.now().strftime("%H:%M:%S")
    ctx.user_data["log"].append(f"[{ts}] {msg}")
    if len(ctx.user_data["log"]) > 50:
        ctx.user_data["log"] = ctx.user_data["log"][-50:]

def ensure_init(ctx):
    d = ctx.user_data
    d.setdefault("api_key",        None)
    d.setdefault("active_numbers", [])
    d.setdefault("log",            [])
    d.setdefault("service",        DEFAULT_SERVICE)
    d.setdefault("country",        DEFAULT_COUNTRY)
    d.setdefault("svc_name",       DEFAULT_SVC_NAME)
    d.setdefault("ctr_name",       DEFAULT_CTR_NAME)
    d.setdefault("price",          0.19)

def fmt_numbers(actives: list) -> str:
    if not actives:
        return "_Belum ada nomor aktif._"
    lines = []
    for i, n in enumerate(actives, 1):
        lines.append(
            f"{i}. 📞 `+{n['phone']}`\n"
            f"   🆔 ID: `{n['id']}` | {n['service']} | {n['country']}\n"
            f"   🕐 {n['time']}"
        )
    return "\n\n".join(lines)

def error_map(msg: str) -> str:
    return {
        "NO_NUMBERS":  "❌ Nomor habis untuk pilihan ini. Coba layanan/negara lain.",
        "NO_BALANCE":  "❌ Saldo tidak cukup. Silakan top up di grizzlysms.com",
        "BAD_KEY":     "❌ API Key tidak valid. Ganti lewat 🔑 Ganti API Key",
        "BAD_SERVICE": "❌ Kode layanan tidak valid.",
        "BAD_COUNTRY": "❌ Kode negara tidak valid.",
        "SERVER_ERROR":"❌ Server error. Coba lagi nanti.",
    }.get(msg, f"❌ Error: {msg}")

# ─── API ────────────────────────────────────────────────────────────────────

def api_call(api_key: str, params: dict) -> str:
    params["api_key"] = api_key.strip()
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    for url in [API_BASE, API_BASE2]:
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=10, verify=False)
            result = r.text.strip()
            # Kalau response mengandung HTML (error page), abaikan
            if result.startswith("<") or len(result) > 200:
                logger.warning(f"Response HTML/terlalu panjang dari {url}, skip")
                continue
            logger.info(f"API [{params.get('action')}] → {result[:80]}")
            return result
        except Exception as e:
            logger.error(f"API error url={url}: {e}")
            continue
    return "SERVER_ERROR"

def api_get_balance(api_key: str):
    key = api_key.strip()
    resp = api_call(key, {"action": "getBalance"})
    logger.info(f"getBalance raw response: '{resp}'")
    if resp.startswith("ACCESS_BALANCE:"):
        try:
            return float(resp.split(":")[1])
        except:
            return 0.0
    return None

def api_buy_number(api_key, service, country, max_price=999):
    resp = api_call(api_key, {"action": "getNumber", "service": service, "country": country, "maxPrice": max_price})
    if resp.startswith("ACCESS_NUMBER:"):
        parts = resp.split(":")
        if len(parts) >= 3:
            return {"status": "ok", "id": parts[1], "phone": parts[2]}
    return {"status": "error", "msg": resp}

def api_get_sms(api_key, activation_id):
    resp = api_call(api_key, {"action": "getStatus", "id": activation_id})
    if resp.startswith("STATUS_OK:"):
        return {"status": "ok", "code": resp.split(":")[1]}
    elif resp in ("STATUS_WAIT_CODE", "STATUS_WAIT_RETRY", "STATUS_WAIT_RESEND"):
        return {"status": "waiting"}
    elif resp == "STATUS_CANCEL":
        return {"status": "cancelled"}
    return {"status": "error", "msg": resp}

def api_cancel(api_key, activation_id):
    resp = api_call(api_key, {"action": "setStatus", "id": activation_id, "status": 8})
    return "ACCESS_CANCEL" in resp or "ACCESS_ACTIVATION" in resp or resp == "1"

def api_confirm(api_key, activation_id):
    resp = api_call(api_key, {"action": "setStatus", "id": activation_id, "status": 6})
    return "ACCESS_ACTIVATION" in resp or resp == "1"

def api_get_price(api_key, service, country):
    resp = api_call(api_key, {"action": "getPrices", "service": service, "country": country})
    try:
        data = json.loads(resp)
        p = data.get(country, {}).get(service, {})
        return {"cost": p.get("cost", "?"), "count": p.get("count", 0)}
    except:
        return {"cost": "?", "count": 0}

# ─── KEYBOARD ───────────────────────────────────────────────────────────────

def main_keyboard(ctx) -> ReplyKeyboardMarkup:
    svc_name = ctx.user_data.get("svc_name", DEFAULT_SVC_NAME)
    price    = ctx.user_data.get("price", "?")
    return ReplyKeyboardMarkup([
        [KeyboardButton("💰 Cek Saldo"),      KeyboardButton("📲 Beli 1 Nomor")],
        [KeyboardButton("🔟 Beli 5 Nomor"),   KeyboardButton("🔢 Beli 3 Nomor")],
        [KeyboardButton(f"📦 Layanan: {svc_name[:8]}..."), KeyboardButton(f"💲 Harga: ${price}")],
        [KeyboardButton("🔑 Ganti API Key")],
        [KeyboardButton("❌ Batalkan Nomor"), KeyboardButton("🗑 Batalkan Semua")],
        [KeyboardButton("📋 Lihat Log")],
    ], resize_keyboard=True)

def setup_keyboard() -> ReplyKeyboardMarkup:
    """Keyboard saat user belum punya API Key."""
    return ReplyKeyboardMarkup([
        [KeyboardButton("🔑 Masukkan API Key")],
        [KeyboardButton("❓ Cara Dapat API Key")],
    ], resize_keyboard=True)

# ─── SETUP API KEY FLOW ─────────────────────────────────────────────────────

async def prompt_api_key(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Minta user input API Key mereka."""
    ctx.user_data["waiting_for"] = "api_key_setup"
    await update.message.reply_text(
        "🔑 *Masukkan API Key GrizzlySMS kamu*\n\n"
        "Cara dapat API Key:\n"
        "1. Daftar/login di grizzlysms.com\n"
        "2. Klik nama profil → *Settings*\n"
        "3. Copy *API Key* yang tersedia\n\n"
        "Lalu ketik/paste API Key kamu di sini 👇\n\n"
        "⚠️ _API Key hanya disimpan untuk akun Telegram kamu sendiri_",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )

# ─── BUY FLOW ───────────────────────────────────────────────────────────────

async def do_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE, qty: int):
    ensure_init(ctx)
    api_key = get_api_key(ctx)
    if not api_key:
        await prompt_api_key(update, ctx)
        return

    service  = ctx.user_data["service"]
    country  = ctx.user_data["country"]
    svc_name = ctx.user_data["svc_name"]
    ctr_name = ctx.user_data["ctr_name"]
    price    = ctx.user_data.get("price", 999)

    msg = await update.message.reply_text(
        f"⏳ Membeli *{qty}x* nomor *{svc_name}* {ctr_name}...",
        parse_mode="Markdown"
    )

    results = []
    for i in range(qty):
        result = api_buy_number(api_key, service, country, max_price=0.20)
        if result["status"] == "ok":
            entry = {
                "id":      result["id"],
                "phone":   result["phone"],
                "service": svc_name,
                "country": ctr_name,
                "time":    datetime.now().strftime("%H:%M:%S"),
            }
            ctx.user_data["active_numbers"].append(entry)
            results.append(entry)
            add_log(ctx, f"BELI OK | ID:{result['id']} | +{result['phone']} | {svc_name} {ctr_name}")

            # Register auto-poll background
            AUTO_POLL_JOBS[result["id"]] = {
                "chat_id":    update.effective_chat.id,
                "api_key":    api_key,
                "phone":      result["phone"],
                "service":    svc_name,
                "country":    ctr_name,
                "start_time": time.time(),
            }
            asyncio.create_task(auto_poll_worker(ctx.application, result["id"]))

            if qty > 1:
                time.sleep(1)
        else:
            add_log(ctx, f"BELI GAGAL | {result.get('msg','?')} | {svc_name} {ctr_name}")
            if qty == 1:
                await msg.edit_text(error_map(result.get("msg", "?")), parse_mode="Markdown")
                return
            results.append({"error": result.get("msg", "?")})

    if qty == 1 and results and "error" not in results[0]:
        n = results[0]
        text = (
            f"✅ *Nomor Berhasil Dibeli!*\n\n"
            f"📞 *Nomor:* `+{n['phone']}`\n"
            f"🆔 *ID Aktivasi:* `{n['id']}`\n"
            f"📦 {n['service']} | {n['country']}\n"
            f"🕐 {n['time']}\n\n"
            f"🔔 *OTP akan dikirim otomatis saat masuk!*\n"
            f"Masukkan nomor ke layanan tujuan sekarang 👆"
        )
    else:
        lines = [f"📊 *Hasil Beli {qty} Nomor*\n"]
        for i, n in enumerate(results, 1):
            if "error" in n:
                lines.append(f"{i}. ❌ Gagal: {n['error']}")
            else:
                lines.append(f"{i}. ✅ `+{n['phone']}` | ID: `{n['id']}`")
        ok = sum(1 for n in results if "error" not in n)
        lines.append(f"\n✅ Berhasil: {ok}/{qty}")
        lines.append(f"🔔 OTP akan dikirim otomatis!")
        text = "\n".join(lines)

    await msg.edit_text(text, parse_mode="Markdown")

# ─── HANDLERS ───────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    ensure_init(ctx)
    user = update.effective_user
    api_key = get_api_key(ctx)

    if not api_key:
        # Belum setup API Key
        await update.message.reply_text(
            f"🐻 *Selamat datang, {user.first_name}!*\n\n"
            f"🆔 Telegram ID kamu: `{user.id}`\n\n"
            "Sebelum mulai, kamu perlu memasukkan *API Key GrizzlySMS* milikmu sendiri.\n"
            "Setiap user pakai API Key & saldo mereka sendiri.\n\n"
            "Klik tombol di bawah untuk mulai 👇",
            parse_mode="Markdown",
            reply_markup=setup_keyboard()
        )
    else:
        bal = api_get_balance(api_key)
        bal_text = f"${bal:.4f}" if bal is not None else "Gagal cek"
        await update.message.reply_text(
            f"🐻 *GrizzlySMS Bot*\n\n"
            f"👤 {user.first_name} | 🆔 `{user.id}`\n"
            f"💰 Saldo: *{bal_text}*\n"
            f"📦 Layanan: *{ctx.user_data['svc_name']}* {ctx.user_data['ctr_name']}\n\n"
            "Pilih menu 👇",
            parse_mode="Markdown",
            reply_markup=main_keyboard(ctx)
        )

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    ensure_init(ctx)
    text = update.message.text or ""
    api_key = get_api_key(ctx)

    # ── Setup mode (belum punya API Key)
    if text == "🔑 Masukkan API Key":
        await prompt_api_key(update, ctx)
        return

    if text == "❓ Cara Dapat API Key":
        await update.message.reply_text(
            "📖 *Cara Dapat API Key GrizzlySMS*\n\n"
            "1. Buka grizzlysms.com\n"
            "2. Daftar akun (gratis)\n"
            "3. Top up saldo (minimal ~$1)\n"
            "4. Klik foto profil → *Settings*\n"
            "5. Copy *API Key* yang tertera\n"
            "6. Paste di bot ini\n\n"
            "Lalu klik *🔑 Masukkan API Key* 👇",
            parse_mode="Markdown",
            reply_markup=setup_keyboard()
        )
        return

    # ── Waiting for API Key input
    if ctx.user_data.get("waiting_for") == "api_key_setup":
        ctx.user_data.pop("waiting_for")
        new_key = text.strip()

        # Validasi format dasar
        if len(new_key) < 10:
            await update.message.reply_text(
                "❌ API Key terlalu pendek. Pastikan copy dengan lengkap.\nCoba lagi 👇",
                parse_mode="Markdown"
            )
            ctx.user_data["waiting_for"] = "api_key_setup"
            return

        msg = await update.message.reply_text(
            f"⏳ Memvalidasi API Key...\n`{new_key[:8]}...{new_key[-4:]}`",
            parse_mode="Markdown"
        )

        # Coba langsung hit API
        raw = api_call(new_key, {"action": "getBalance"})
        logger.info(f"Validasi API Key user {update.effective_user.id}: raw='{raw}'")

        if raw.startswith("ACCESS_BALANCE:"):
            try:
                bal = float(raw.split(":")[1])
            except:
                bal = 0.0
            ctx.user_data["api_key"] = new_key
            add_log(ctx, f"API KEY SETUP | saldo: ${bal:.4f}")
            await msg.edit_text(
                f"✅ *API Key berhasil disimpan!*\n\n"
                f"💰 Saldo kamu: *${bal:.4f}*\n\n"
                f"Sekarang kamu bisa mulai beli nomor! 🎉",
                parse_mode="Markdown"
            )
            await update.message.reply_text("Pilih menu di bawah 👇", reply_markup=main_keyboard(ctx))
        else:
            await msg.edit_text(
                f"❌ *API Key gagal divalidasi*\n\n"
                f"Pastikan API Key benar dari grizzlysms.com → Settings\n\n"
                f"Coba lagi 👇",
                parse_mode="Markdown"
            )
            ctx.user_data["waiting_for"] = "api_key_setup"
        return

    # ── Waiting for cancel select
    if ctx.user_data.get("waiting_for") == "cancel_select":
        ctx.user_data.pop("waiting_for")
        actives = ctx.user_data.get("active_numbers", [])
        try:
            idx = int(text.strip()) - 1
            if 0 <= idx < len(actives):
                n = actives[idx]
                msg = await update.message.reply_text(f"⏳ Membatalkan `+{n['phone']}`...", parse_mode="Markdown")
                if api_cancel(api_key, n["id"]):
                    ctx.user_data["active_numbers"].pop(idx)
                    add_log(ctx, f"CANCEL OK | ID:{n['id']} | +{n['phone']}")
                    await msg.edit_text(f"✅ Nomor `+{n['phone']}` berhasil dibatalkan.", parse_mode="Markdown")
                else:
                    await msg.edit_text(f"❌ Gagal membatalkan.", parse_mode="Markdown")
            else:
                await update.message.reply_text("❌ Nomor urut tidak valid.")
        except:
            await update.message.reply_text("❌ Masukkan angka sesuai urutan.")
        return

    # ── Waiting for ganti API Key (dari menu)
    if ctx.user_data.get("waiting_for") == "api_key_change":
        ctx.user_data.pop("waiting_for")
        new_key = text.strip()
        msg = await update.message.reply_text("⏳ Memvalidasi API Key...")
        bal = api_get_balance(new_key)
        if bal is not None:
            ctx.user_data["api_key"] = new_key
            add_log(ctx, f"API KEY GANTI | saldo: ${bal:.4f}")
            await msg.edit_text(
                f"✅ *API Key berhasil diperbarui!*\n\n💰 Saldo: *${bal:.4f}*",
                parse_mode="Markdown"
            )
        else:
            await msg.edit_text("❌ API Key tidak valid. Coba lagi.", parse_mode="Markdown")
            ctx.user_data["waiting_for"] = "api_key_change"
        return

    # ── Kalau belum ada API Key, arahkan setup
    if not api_key:
        await update.message.reply_text(
            "⚠️ Kamu belum memasukkan API Key!\nKlik tombol di bawah 👇",
            reply_markup=setup_keyboard()
        )
        return

    # ── Menu utama
    if "Cek Saldo" in text:
        msg = await update.message.reply_text("⏳ Mengecek saldo...")
        bal = api_get_balance(api_key)
        if bal is not None:
            add_log(ctx, f"CEK SALDO: ${bal:.4f}")
            await msg.edit_text(
                f"💰 *Saldo GrizzlySMS*\n\n*${bal:.4f}*\n\nTop up: grizzlysms.com",
                parse_mode="Markdown"
            )
        else:
            await msg.edit_text("❌ Gagal cek saldo. Coba ganti API Key.")

    elif "Beli 1 Nomor" in text:
        await do_buy(update, ctx, 1)

    elif "Beli 3 Nomor" in text:
        await do_buy(update, ctx, 3)

    elif "Beli 5 Nomor" in text:
        await do_buy(update, ctx, 5)

    elif text.startswith("📦 Layanan"):
        svc = ctx.user_data.get("svc_name", DEFAULT_SVC_NAME)
        ctr = ctx.user_data.get("ctr_name", DEFAULT_CTR_NAME)
        p   = ctx.user_data.get("price", "?")
        await update.message.reply_text(
            f"📦 *Layanan Aktif*\n\n"
            f"Layanan : *{svc}*\n"
            f"Negara  : *{ctr}*\n"
            f"Harga   : *${p}*\n\n"
            f"Ganti dengan perintah:\n"
            f"`/setlayanan <kode_svc> <kode_negara> <nama_svc> <nama_negara>`\n\n"
            f"*Contoh populer:*\n"
            f"`/setlayanan wa 18 WhatsApp Vietnam` — ~$0.19\n"
            f"`/setlayanan wa 6 WhatsApp Indonesia` — ~$0.62\n"
            f"`/setlayanan tg 18 Telegram Vietnam`\n"
            f"`/setlayanan go 6 Google Indonesia`",
            parse_mode="Markdown"
        )

    elif text.startswith("💲 Harga"):
        msg = await update.message.reply_text("⏳ Mengambil harga...")
        info = api_get_price(api_key, ctx.user_data["service"], ctx.user_data["country"])
        await msg.edit_text(
            f"💲 *Harga Saat Ini*\n\n"
            f"📦 {ctx.user_data['svc_name']} | {ctx.user_data['ctr_name']}\n"
            f"💰 Harga    : *${info['cost']}*\n"
            f"📊 Tersedia : *{info['count']}* nomor",
            parse_mode="Markdown"
        )

    elif "Ganti API Key" in text:
        ctx.user_data["waiting_for"] = "api_key_change"
        await update.message.reply_text(
            "🔑 *Ganti API Key*\n\nMasukkan API Key GrizzlySMS baru kamu:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )

    elif text == "❌ Batalkan Nomor":
        actives = ctx.user_data.get("active_numbers", [])
        if not actives:
            await update.message.reply_text("ℹ️ Tidak ada nomor aktif.")
            return
        lines = ["📋 *Daftar Nomor Aktif*\n\nBalas dengan nomor urut:\n"]
        for i, n in enumerate(actives, 1):
            lines.append(f"{i}. `+{n['phone']}` | ID `{n['id']}` | {n['service']}")
        lines.append("\n_Ketik angka untuk batalkan. Contoh: `1`_")
        ctx.user_data["waiting_for"] = "cancel_select"
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    elif "Batalkan Semua" in text:
        actives = ctx.user_data.get("active_numbers", [])
        if not actives:
            await update.message.reply_text("ℹ️ Tidak ada nomor aktif.")
            return
        msg = await update.message.reply_text(f"⏳ Membatalkan {len(actives)} nomor...")
        success = 0
        for n in actives:
            if api_cancel(api_key, n["id"]):
                success += 1
                add_log(ctx, f"CANCEL OK | ID:{n['id']} | +{n['phone']}")
            time.sleep(0.5)
        ctx.user_data["active_numbers"] = []
        await msg.edit_text(f"✅ Selesai! Berhasil batalkan: {success}/{len(actives)} nomor")

    elif "Lihat Log" in text:
        logs = ctx.user_data.get("log", [])
        if not logs:
            await update.message.reply_text("📋 Log kosong.")
            return
        log_text = "\n".join(logs[-20:])
        await update.message.reply_text(
            f"📋 *Log Aktivitas (20 terbaru)*\n\n`{log_text}`",
            parse_mode="Markdown"
        )

    else:
        await update.message.reply_text("❓ Gunakan menu di bawah.", reply_markup=main_keyboard(ctx))

# ─── SLASH COMMANDS ─────────────────────────────────────────────────────────

async def ceksms_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update): return
    ensure_init(ctx)
    api_key = get_api_key(ctx)
    if not api_key:
        await prompt_api_key(update, ctx)
        return
    args = ctx.args
    actives = ctx.user_data.get("active_numbers", [])
    if not args:
        if not actives:
            await update.message.reply_text("Gunakan: `/ceksms <ID_aktivasi>`", parse_mode="Markdown")
            return
        lines = ["📋 *Nomor Aktif*\n"]
        for n in actives:
            lines.append(f"• `+{n['phone']}` | ID: `{n['id']}` | {n['service']}\n  `/ceksms {n['id']}`")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return
    await poll_sms(update, ctx, args[0].strip())

async def poll_sms(update, ctx, activation_id: str):
    api_key = get_api_key(ctx)
    msg = await update.message.reply_text(
        f"⏳ Menunggu SMS untuk ID `{activation_id}`...\n"
        f"_Auto-cek tiap {SMS_POLL_INTERVAL}s, maks {SMS_MAX_WAIT//60} menit_",
        parse_mode="Markdown"
    )
    start_time = time.time()
    attempt = 0
    while time.time() - start_time < SMS_MAX_WAIT:
        attempt += 1
        result = api_get_sms(api_key, activation_id)
        if result["status"] == "ok":
            code = result["code"]
            elapsed = int(time.time() - start_time)
            add_log(ctx, f"SMS OK | ID:{activation_id} | kode:{code}")
            await msg.edit_text(
                f"🎉 *SMS Diterima!*\n\n"
                f"🔑 *Kode OTP:* `{code}`\n"
                f"🆔 ID: `{activation_id}`\n"
                f"⏱️ Waktu tunggu: {elapsed}s\n\n"
                f"Setelah verifikasi berhasil:\n`/konfirmasi {activation_id}`",
                parse_mode="Markdown"
            )
            return
        elif result["status"] == "cancelled":
            await msg.edit_text(f"❌ Aktivasi `{activation_id}` dibatalkan.", parse_mode="Markdown")
            return
        elif result["status"] == "error":
            await msg.edit_text(f"❌ Error. Pastikan ID `{activation_id}` benar.", parse_mode="Markdown")
            return
        elapsed = int(time.time() - start_time)
        await msg.edit_text(
            f"⏳ Menunggu SMS untuk ID `{activation_id}`...\n"
            f"Percobaan ke-{attempt} | ⏱️ {elapsed}s",
            parse_mode="Markdown"
        )
        time.sleep(SMS_POLL_INTERVAL)
    await msg.edit_text(
        f"⏰ *Timeout!* SMS tidak datang dalam {SMS_MAX_WAIT//60} menit.\n"
        f"Coba `/cancel {activation_id}` untuk batalkan.",
        parse_mode="Markdown"
    )

async def cancel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update): return
    ensure_init(ctx)
    api_key = get_api_key(ctx)
    if not api_key:
        await prompt_api_key(update, ctx); return
    args = ctx.args
    if not args:
        await update.message.reply_text("Gunakan: `/cancel <ID_aktivasi>`", parse_mode="Markdown"); return
    activation_id = args[0].strip()
    msg = await update.message.reply_text(f"⏳ Membatalkan `{activation_id}`...", parse_mode="Markdown")
    if api_cancel(api_key, activation_id):
        ctx.user_data["active_numbers"] = [n for n in ctx.user_data.get("active_numbers", []) if n["id"] != activation_id]
        add_log(ctx, f"CANCEL CMD OK | ID:{activation_id}")
        await msg.edit_text(f"✅ Aktivasi `{activation_id}` berhasil dibatalkan.", parse_mode="Markdown")
    else:
        await msg.edit_text(f"❌ Gagal membatalkan `{activation_id}`.", parse_mode="Markdown")

async def konfirmasi_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update): return
    ensure_init(ctx)
    api_key = get_api_key(ctx)
    if not api_key:
        await prompt_api_key(update, ctx); return
    args = ctx.args
    if not args:
        await update.message.reply_text("Gunakan: `/konfirmasi <ID_aktivasi>`", parse_mode="Markdown"); return
    activation_id = args[0].strip()
    msg = await update.message.reply_text(f"⏳ Konfirmasi `{activation_id}`...", parse_mode="Markdown")
    if api_confirm(api_key, activation_id):
        ctx.user_data["active_numbers"] = [n for n in ctx.user_data.get("active_numbers", []) if n["id"] != activation_id]
        add_log(ctx, f"KONFIRMASI OK | ID:{activation_id}")
        await msg.edit_text(f"✅ Aktivasi `{activation_id}` dikonfirmasi!", parse_mode="Markdown")
    else:
        await msg.edit_text(f"❌ Gagal konfirmasi `{activation_id}`.", parse_mode="Markdown")

async def setlayanan_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update): return
    ensure_init(ctx)
    api_key = get_api_key(ctx)
    if not api_key:
        await prompt_api_key(update, ctx); return
    args = ctx.args
    if len(args) < 4:
        await update.message.reply_text(
            "❌ Format salah!\n\n"
            "Gunakan:\n`/setlayanan <kode_svc> <kode_negara> <nama_svc> <nama_negara>`\n\n"
            "*Contoh populer:*\n"
            "`/setlayanan wa 18 WhatsApp Vietnam` — ~$0.19\n"
            "`/setlayanan wa 6 WhatsApp Indonesia` — ~$0.62\n"
            "`/setlayanan tg 18 Telegram Vietnam`\n"
            "`/setlayanan go 6 Google Indonesia`\n"
            "`/setlayanan fb 6 Facebook Indonesia`\n\n"
            "*Kode Layanan:* `wa` `tg` `go` `fb` `ig` `tt` `pp` `am`\n"
            "*Kode Negara:* `0`=USA `6`=ID `18`=VN `22`=UK `32`=IN",
            parse_mode="Markdown"
        )
        return
    svc_code, ctr_code, svc_name, ctr_name = args[0], args[1], args[2], args[3]
    msg = await update.message.reply_text("⏳ Mengecek harga...")
    info = api_get_price(api_key, svc_code, ctr_code)
    ctx.user_data.update({
        "service": svc_code, "country": ctr_code,
        "svc_name": svc_name, "ctr_name": ctr_name,
        "price": info["cost"]
    })
    add_log(ctx, f"SET LAYANAN | {svc_name} {ctr_name} ${info['cost']}")
    await msg.edit_text(
        f"✅ *Layanan diperbarui!*\n\n"
        f"📦 Layanan: *{svc_name}*\n"
        f"🌍 Negara : *{ctr_name}*\n"
        f"💰 Harga  : *${info['cost']}*\n"
        f"📊 Tersedia: *{info['count']}* nomor",
        parse_mode="Markdown",
        reply_markup=main_keyboard(ctx)
    )

async def daftar_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update): return
    ensure_init(ctx)
    actives = ctx.user_data.get("active_numbers", [])
    await update.message.reply_text(
        f"📋 *Nomor Aktif ({len(actives)})*\n\n{fmt_numbers(actives)}",
        parse_mode="Markdown"
    )

async def myid_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Tampilkan Telegram ID user."""
    user = update.effective_user
    await update.message.reply_text(
        f"🆔 *Telegram ID kamu:*\n\n`{user.id}`\n\n"
        f"Nama: {user.first_name}\n"
        f"Username: @{user.username or '-'}",
        parse_mode="Markdown"
    )

# ─── AUTO POLL BACKGROUND ───────────────────────────────────────────────────

async def auto_poll_worker(app, activation_id: str):
    """Background task: auto cek SMS dan kirim notif ke user."""
    job = AUTO_POLL_JOBS.get(activation_id)
    if not job:
        return

    chat_id  = job["chat_id"]
    api_key  = job["api_key"]
    phone    = job["phone"]
    service  = job["service"]
    country  = job["country"]
    start_t  = job["start_time"]

    while True:
        # Cek timeout
        if time.time() - start_t > SMS_MAX_WAIT:
            AUTO_POLL_JOBS.pop(activation_id, None)
            await app.bot.send_message(
                chat_id=chat_id,
                text=f"⏰ *Timeout!*\n\n"
                     f"SMS tidak masuk dalam {SMS_MAX_WAIT//60} menit\n"
                     f"📞 `+{phone}` | ID: `{activation_id}`\n\n"
                     f"Gunakan `/cancel {activation_id}` untuk batalkan.",
                parse_mode="Markdown"
            )
            return

        result = api_get_sms(api_key, activation_id)

        if result["status"] == "ok":
            code = result["code"]
            AUTO_POLL_JOBS.pop(activation_id, None)
            await app.bot.send_message(
                chat_id=chat_id,
                text=f"🔔 *OTP MASUK!*\n\n"
                     f"📞 Nomor: `+{phone}`\n"
                     f"🔑 Kode OTP: `{code}`\n"
                     f"📦 Layanan: {service}\n"
                     f"🌍 {country}\n\n"
                     f"✅ Setelah verifikasi: `/konfirmasi {activation_id}`",
                parse_mode="Markdown"
            )
            return

        elif result["status"] == "cancelled":
            AUTO_POLL_JOBS.pop(activation_id, None)
            await app.bot.send_message(
                chat_id=chat_id,
                text=f"❌ Aktivasi `{activation_id}` dibatalkan.",
                parse_mode="Markdown"
            )
            return

        elif result["status"] == "error":
            AUTO_POLL_JOBS.pop(activation_id, None)
            return

        await asyncio.sleep(SMS_POLL_INTERVAL)

# ─── MAIN ───────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",       start))
    app.add_handler(CommandHandler("myid",        myid_cmd))
    app.add_handler(CommandHandler("ceksms",      ceksms_cmd))
    app.add_handler(CommandHandler("cancel",      cancel_cmd))
    app.add_handler(CommandHandler("konfirmasi",  konfirmasi_cmd))
    app.add_handler(CommandHandler("setlayanan",  setlayanan_cmd))
    app.add_handler(CommandHandler("daftar",      daftar_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("🐻 GrizzlySMS Bot v3 aktif!")
    print(f"Whitelist: {ALLOWED_IDS if ALLOWED_IDS else 'Semua user (tidak ada whitelist)'}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
