#!/usr/bin/env python3
"""
🐻 GrizzlySMS Telegram Bot v4
CHANGELOG v4:
- [FIX KRITIS] API Key + params dikirim via URL manual (bukan requests params=)
  → Solusi NO_BALANCE & NO_NUMBERS meski saldo/nomor tersedia
- [FIX] Hapus maxPrice sepenuhnya dari getNumber
- [FIX] Parse ACCESS_NUMBER dengan maxsplit agar phone tidak terpotong
- [FIX] time.sleep() diganti asyncio.sleep() di semua async function
- [FITUR] Auto OTP: OTP masuk langsung notif ke bot TANPA perintah /ceksms
- [FITUR] Tombol 💲 Harga & 🌍 Negara di keyboard
"""

import logging
import requests
import asyncio
import json
import os
import urllib.parse
import time
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler,
    MessageHandler, filters, ContextTypes
)

# ─── KONFIGURASI ─────────────────────────────────────────────────────────────

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8762689776:AAGmCnAH_WP6yhcH4EwpTPFxi8Ar0tW54IY")

ALLOWED_IDS = [
    7052770466,
    # tambah ID lain di sini
]

API_BASE  = "https://grizzlysms.com/stubs/handler_api.php"
API_BASE2 = "https://api.grizzlysms.com/stubs/handler_api.php"

DEFAULT_SERVICE  = "wa"
DEFAULT_COUNTRY  = "18"
DEFAULT_SVC_NAME = "WhatsApp"
DEFAULT_CTR_NAME = "🇻🇳 Vietnam"

SMS_POLL_INTERVAL = 5    # detik antar cek OTP
SMS_MAX_WAIT      = 300  # timeout 5 menit

# Storage background polling {activation_id: {chat_id, api_key, phone, ...}}
AUTO_POLL_JOBS: dict = {}

# ─── LOGGING ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── WHITELIST ────────────────────────────────────────────────────────────────

def is_allowed(user_id: int) -> bool:
    return True if not ALLOWED_IDS else user_id in ALLOWED_IDS

async def check_access(update: Update) -> bool:
    user = update.effective_user
    if not is_allowed(user.id):
        await update.message.reply_text(
            f"🚫 *Akses Ditolak*\n\nID kamu: `{user.id}`\nHubungi admin.",
            parse_mode="Markdown"
        )
        return False
    return True

# ─── HELPER ──────────────────────────────────────────────────────────────────

def get_api_key(ctx) -> str | None:
    return ctx.user_data.get("api_key")

def add_log(ctx, msg: str):
    ctx.user_data.setdefault("log", [])
    ts = datetime.now().strftime("%H:%M:%S")
    ctx.user_data["log"].append(f"[{ts}] {msg}")
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
    d.setdefault("price",          "?")

def fmt_numbers(actives: list) -> str:
    if not actives:
        return "_Belum ada nomor aktif._"
    lines = []
    for i, n in enumerate(actives, 1):
        lines.append(
            f"{i}. 📞 `+{n['phone']}`\n"
            f"   🆔 `{n['id']}` | {n['service']} | {n['country']}\n"
            f"   🕐 {n['time']}"
        )
    return "\n\n".join(lines)

def error_map(raw: str) -> str:
    MAP = {
        "NO_NUMBERS":                  "❌ Nomor habis. Coba layanan/negara lain.",
        "NO_BALANCE":                  "❌ Saldo tidak cukup. Top up di grizzlysms.com",
        "BAD_KEY":                     "❌ API Key tidak valid.",
        "BAD_SERVICE":                 "❌ Kode layanan tidak valid.",
        "BAD_COUNTRY":                 "❌ Kode negara tidak valid.",
        "SERVER_ERROR":                "❌ Server error. Coba lagi.",
        "TOO_MANY_ACTIVE_ACTIVATIONS": "❌ Terlalu banyak aktivasi. Batalkan dulu.",
        "FORMAT_ERROR":                "❌ Format response tidak dikenal.",
        "PRICE_TOO_HIGH":              "❌ Harga nomor di atas $0.19. Bot hanya beli ≤ $0.19.",
    }
    return MAP.get(raw, f"❌ Error: `{raw}`")

# ─── API CALL (FIX UTAMA: URL manual, bukan params=dict) ─────────────────────

def api_call(api_key: str, action: str, extra: dict = None) -> str:
    """
    ✅ FIX KRITIS: Bangun URL secara manual dengan urllib.parse.urlencode
    Masalah sebelumnya: requests.get(params={...}) kadang encode API Key
    dengan cara yang berbeda dari yang diharapkan GrizzlySMS, sehingga
    server menganggap key tidak valid → NO_BALANCE / BAD_KEY.
    """
    params = {"api_key": api_key.strip(), "action": action}
    if extra:
        params.update(extra)

    query = urllib.parse.urlencode(params)

    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    for base_url in [API_BASE, API_BASE2]:
        url = f"{base_url}?{query}"
        try:
            r = requests.get(url, timeout=12, verify=False, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            })
            result = r.text.strip()

            # Skip jika HTML (error page)
            if result.startswith("<") or result.startswith("<!"):
                logger.warning(f"HTML response dari {base_url}, skip")
                continue

            logger.info(f"[{action}] {base_url.split('/')[2]} → {result[:100]}")
            return result

        except requests.exceptions.Timeout:
            logger.warning(f"Timeout dari {base_url}")
            continue
        except Exception as e:
            logger.error(f"Request error {base_url}: {e}")
            continue

    return "SERVER_ERROR"

# ─── API FUNCTIONS ────────────────────────────────────────────────────────────

def api_get_balance(api_key: str) -> float | None:
    resp = api_call(api_key, "getBalance")
    if resp.startswith("ACCESS_BALANCE:"):
        try:
            return float(resp.split(":")[1])
        except:
            return 0.0
    logger.warning(f"getBalance gagal: {resp}")
    return None

MAX_PRICE = 0.20  # Hanya beli nomor harga <= $0.19

def api_buy_number(api_key: str, service: str, country: str) -> dict:
    # Cek harga dulu sebelum beli
    price_info = api_get_price(api_key, service, country)
    try:
        cost = float(price_info.get("cost", 999))
    except (ValueError, TypeError):
        cost = 999.0

    if cost > MAX_PRICE:
        logger.warning(f"Harga ${cost} > ${MAX_PRICE}, skip beli")
        return {"status": "error", "msg": "PRICE_TOO_HIGH", "cost": cost}

    # Beli nomor tanpa maxPrice
    resp = api_call(api_key, "getNumber", {"service": service, "country": country})

    if resp.startswith("ACCESS_NUMBER:"):
        parts = resp.split(":", 2)
        if len(parts) == 3:
            act_id = parts[1].strip()
            phone  = parts[2].strip()
            logger.info(f"Beli OK id={act_id} phone={phone} harga=${cost}")
            return {"status": "ok", "id": act_id, "phone": phone, "cost": cost}
        logger.error(f"Format tidak dikenal: {resp}")
        return {"status": "error", "msg": "FORMAT_ERROR"}

    logger.warning(f"Beli gagal: {resp}")
    return {"status": "error", "msg": resp.split(":")[0] if ":" in resp else resp}

def api_get_sms(api_key: str, activation_id: str) -> dict:
    resp = api_call(api_key, "getStatus", {"id": activation_id})
    if resp.startswith("STATUS_OK:"):
        return {"status": "ok", "code": resp.split(":", 1)[1]}
    elif resp in ("STATUS_WAIT_CODE", "STATUS_WAIT_RETRY", "STATUS_WAIT_RESEND"):
        return {"status": "waiting"}
    elif resp == "STATUS_CANCEL":
        return {"status": "cancelled"}
    return {"status": "error", "msg": resp}

def api_cancel(api_key: str, activation_id: str) -> bool:
    resp = api_call(api_key, "setStatus", {"id": activation_id, "status": "8"})
    return any(x in resp for x in ["ACCESS_CANCEL", "ACCESS_ACTIVATION", "1"])

def api_confirm(api_key: str, activation_id: str) -> bool:
    resp = api_call(api_key, "setStatus", {"id": activation_id, "status": "6"})
    return any(x in resp for x in ["ACCESS_ACTIVATION", "1"])

def api_get_price(api_key: str, service: str, country: str) -> dict:
    resp = api_call(api_key, "getPrices", {"service": service, "country": country})
    try:
        data = json.loads(resp)
        p = data.get(str(country), {}).get(str(service), {})
        return {"cost": p.get("cost", "?"), "count": p.get("count", 0)}
    except:
        return {"cost": "?", "count": 0}

# ─── KEYBOARD ─────────────────────────────────────────────────────────────────

def main_keyboard(ctx) -> ReplyKeyboardMarkup:
    svc   = ctx.user_data.get("svc_name", DEFAULT_SVC_NAME)
    price = ctx.user_data.get("price", "?")
    return ReplyKeyboardMarkup([
        [KeyboardButton("💰 Cek Saldo"),        KeyboardButton("📲 Beli 1 Nomor")],
        [KeyboardButton("🔟 Beli 5 Nomor"),     KeyboardButton("🔢 Beli 3 Nomor")],
        [KeyboardButton(f"📦 Layanan: {svc[:8]}..."), KeyboardButton(f"💲 Harga: ${price}")],
        [KeyboardButton("🔑 Ganti API Key")],
        [KeyboardButton("❌ Batalkan Nomor..."), KeyboardButton("🗑 Batalkan Semua")],
        [KeyboardButton("📋 Lihat Log")],
    ], resize_keyboard=True)

def setup_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        [KeyboardButton("🔑 Masukkan API Key")],
        [KeyboardButton("❓ Cara Dapat API Key")],
    ], resize_keyboard=True)

# ─── AUTO POLL (OTP MASUK OTOMATIS) ──────────────────────────────────────────

async def auto_poll_worker(app: Application, activation_id: str):
    """
    ✅ FITUR: Background task — cek OTP tiap 5 detik, kirim notif otomatis.
    User tidak perlu ketik /ceksms, OTP langsung muncul di bot.
    """
    job = AUTO_POLL_JOBS.get(activation_id)
    if not job:
        return

    chat_id  = job["chat_id"]
    api_key  = job["api_key"]
    phone    = job["phone"]
    service  = job["service"]
    country  = job["country"]
    start_t  = job["start_time"]

    logger.info(f"Auto-poll mulai: id={activation_id} phone={phone}")

    while activation_id in AUTO_POLL_JOBS:
        elapsed = time.time() - start_t

        # Timeout
        if elapsed > SMS_MAX_WAIT:
            AUTO_POLL_JOBS.pop(activation_id, None)
            try:
                await app.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"⏰ *Timeout OTP*\n\n"
                        f"📞 `+{phone}`\n"
                        f"🆔 `{activation_id}`\n"
                        f"SMS tidak masuk dalam {SMS_MAX_WAIT//60} menit.\n\n"
                        f"Gunakan `/cancel {activation_id}` untuk batalkan."
                    ),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Gagal kirim timeout notif: {e}")
            return

        result = api_get_sms(api_key, activation_id)

        if result["status"] == "ok":
            code = result["code"]
            AUTO_POLL_JOBS.pop(activation_id, None)
            logger.info(f"OTP diterima: id={activation_id} code={code}")
            try:
                await app.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"🔔 *OTP MASUK!*\n\n"
                        f"📞 Nomor : `+{phone}`\n"
                        f"🔑 *Kode OTP : `{code}`*\n"
                        f"📦 Layanan : {service}\n"
                        f"🌍 Negara  : {country}\n"
                        f"⏱️ Waktu   : {int(elapsed)}s\n\n"
                        f"✅ Setelah verifikasi berhasil:\n"
                        f"`/konfirmasi {activation_id}`"
                    ),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Gagal kirim OTP notif: {e}")
            return

        elif result["status"] == "cancelled":
            AUTO_POLL_JOBS.pop(activation_id, None)
            try:
                await app.bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ Aktivasi `{activation_id}` dibatalkan.",
                    parse_mode="Markdown"
                )
            except:
                pass
            return

        elif result["status"] == "error":
            AUTO_POLL_JOBS.pop(activation_id, None)
            logger.warning(f"Auto-poll error: id={activation_id} msg={result.get('msg')}")
            return

        await asyncio.sleep(SMS_POLL_INTERVAL)

def start_poll(app: Application, activation_id: str, chat_id: int,
               api_key: str, phone: str, service: str, country: str):
    """Daftarkan job dan mulai background task."""
    AUTO_POLL_JOBS[activation_id] = {
        "chat_id":    chat_id,
        "api_key":    api_key,
        "phone":      phone,
        "service":    service,
        "country":    country,
        "start_time": time.time(),
    }
    asyncio.create_task(auto_poll_worker(app, activation_id))
    logger.info(f"Poll job registered: {activation_id}")

# ─── BUY FLOW ─────────────────────────────────────────────────────────────────

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

    msg = await update.message.reply_text(
        f"⏳ Membeli *{qty}x* nomor *{svc_name}* {ctr_name}...",
        parse_mode="Markdown"
    )

    results = []
    for i in range(qty):
        result = api_buy_number(api_key, service, country)

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
            add_log(ctx, f"BELI OK | {result['id']} | +{result['phone']}")

            # Mulai auto-poll OTP langsung
            start_poll(
                app=ctx.application,
                activation_id=result["id"],
                chat_id=update.effective_chat.id,
                api_key=api_key,
                phone=result["phone"],
                service=svc_name,
                country=ctr_name,
            )

            if qty > 1:
                await asyncio.sleep(1.2)
        else:
            err = result.get("msg", "UNKNOWN")
            add_log(ctx, f"BELI GAGAL | {err}")
            if qty == 1:
                if err == "PRICE_TOO_HIGH":
                    cost = result.get("cost", "?")
                    txt = "\u274c *Harga terlalu mahal!*\n\nHarga saat ini: *$" + str(cost) + "*\nBatas maksimal: *$0.19*\n\nBot hanya beli nomor \u2264 $0.19."
                    await msg.edit_text(txt, parse_mode="Markdown")
                else:
                    await msg.edit_text(error_map(err), parse_mode="Markdown")
                return
            if err == "PRICE_TOO_HIGH":
                cost = result.get("cost", "?")
                results.append({"error": f"Harga ${cost} > $0.19"})
            else:
                results.append({"error": err})

    # Tampilkan hasil
    if qty == 1 and results and "error" not in results[0]:
        n = results[0]
        text = (
            f"✅ *Nomor Berhasil Dibeli!*\n\n"
            f"📞 *Nomor:* `+{n['phone']}`\n"
            f"🆔 *ID:* `{n['id']}`\n"
            f"📦 {n['service']} | {n['country']}\n"
            f"🕐 {n['time']}\n\n"
            f"🔔 *OTP akan dikirim otomatis ke sini!*\n"
            f"Masukkan nomor ke layanan tujuan sekarang 👆"
        )
    else:
        lines = [f"📊 *Hasil Beli {qty} Nomor*\n"]
        for i, n in enumerate(results, 1):
            if "error" in n:
                lines.append(f"{i}. {error_map(n['error'])}")
            else:
                lines.append(
                    f"{i}. ✅ `+{n['phone']}`\n"
                    f"   🆔 `{n['id']}`"
                )
        ok = sum(1 for n in results if "error" not in n)
        lines.append(f"\n✅ *Berhasil: {ok}/{qty}*")
        if ok > 0:
            lines.append("🔔 OTP akan dikirim otomatis!")
        text = "\n".join(lines)

    await msg.edit_text(text, parse_mode="Markdown")

# ─── PROMPT API KEY ───────────────────────────────────────────────────────────

async def prompt_api_key(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["waiting_for"] = "api_key_setup"
    await update.message.reply_text(
        "🔑 *Masukkan API Key GrizzlySMS kamu*\n\n"
        "1. Login di grizzlysms.com\n"
        "2. Profil → *Settings*\n"
        "3. Copy *API Key*\n\n"
        "Paste di sini 👇",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )

# ─── HANDLERS ─────────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update): return
    ensure_init(ctx)
    user    = update.effective_user
    api_key = get_api_key(ctx)

    if not api_key:
        await update.message.reply_text(
            f"🐻 *Selamat datang, {user.first_name}!*\n\n"
            f"🆔 ID Telegram: `{user.id}`\n\n"
            "Masukkan *API Key GrizzlySMS* kamu untuk mulai.",
            parse_mode="Markdown",
            reply_markup=setup_keyboard()
        )
    else:
        bal = api_get_balance(api_key)
        bal_text = f"${bal:.4f}" if bal is not None else "Gagal cek"
        await update.message.reply_text(
            f"🐻 *GrizzlySMS Bot v4*\n\n"
            f"👤 {user.first_name} | 🆔 `{user.id}`\n"
            f"💰 Saldo: *{bal_text}*\n"
            f"📦 {ctx.user_data['svc_name']} | {ctx.user_data['ctr_name']}",
            parse_mode="Markdown",
            reply_markup=main_keyboard(ctx)
        )

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update): return
    ensure_init(ctx)
    text    = (update.message.text or "").strip()
    api_key = get_api_key(ctx)

    # ── Setup keyboard (belum ada API key)
    if text == "🔑 Masukkan API Key":
        await prompt_api_key(update, ctx); return

    if text == "❓ Cara Dapat API Key":
        await update.message.reply_text(
            "📖 *Cara Dapat API Key*\n\n"
            "1. Buka grizzlysms.com\n"
            "2. Daftar & top up saldo\n"
            "3. Profil → Settings → Copy API Key\n"
            "4. Paste di bot ini",
            parse_mode="Markdown",
            reply_markup=setup_keyboard()
        ); return

    # ── Waiting states
    waiting = ctx.user_data.get("waiting_for")

    if waiting == "api_key_setup":
        ctx.user_data.pop("waiting_for")
        new_key = text.strip()
        if len(new_key) < 10:
            await update.message.reply_text("❌ API Key terlalu pendek. Coba lagi.")
            ctx.user_data["waiting_for"] = "api_key_setup"; return

        msg = await update.message.reply_text(f"⏳ Validasi API Key `{new_key[:6]}...`", parse_mode="Markdown")
        bal = api_get_balance(new_key)
        if bal is not None:
            ctx.user_data["api_key"] = new_key
            add_log(ctx, f"API KEY SETUP | saldo ${bal:.4f}")
            await msg.edit_text(f"✅ *API Key disimpan!*\n\n💰 Saldo: *${bal:.4f}*", parse_mode="Markdown")
            await update.message.reply_text("Pilih menu 👇", reply_markup=main_keyboard(ctx))
        else:
            await msg.edit_text("❌ API Key tidak valid. Pastikan dari grizzlysms.com → Settings\n\nCoba lagi 👇", parse_mode="Markdown")
            ctx.user_data["waiting_for"] = "api_key_setup"
        return

    if waiting == "api_key_change":
        ctx.user_data.pop("waiting_for")
        new_key = text.strip()
        msg = await update.message.reply_text("⏳ Validasi...")
        bal = api_get_balance(new_key)
        if bal is not None:
            ctx.user_data["api_key"] = new_key
            add_log(ctx, f"API KEY GANTI | saldo ${bal:.4f}")
            await msg.edit_text(f"✅ *API Key diperbarui!*\n💰 Saldo: *${bal:.4f}*", parse_mode="Markdown", reply_markup=main_keyboard(ctx))
        else:
            await msg.edit_text("❌ Tidak valid. Coba lagi.")
            ctx.user_data["waiting_for"] = "api_key_change"
        return

    if waiting == "cancel_select":
        ctx.user_data.pop("waiting_for")
        actives = ctx.user_data.get("active_numbers", [])
        try:
            idx = int(text) - 1
            if 0 <= idx < len(actives):
                n = actives[idx]
                msg = await update.message.reply_text(f"⏳ Membatalkan `+{n['phone']}`...", parse_mode="Markdown")
                if api_cancel(api_key, n["id"]):
                    AUTO_POLL_JOBS.pop(n["id"], None)
                    ctx.user_data["active_numbers"].pop(idx)
                    add_log(ctx, f"CANCEL | {n['id']}")
                    await msg.edit_text(f"✅ `+{n['phone']}` dibatalkan.", parse_mode="Markdown")
                else:
                    await msg.edit_text("❌ Gagal batalkan.")
            else:
                await update.message.reply_text("❌ Nomor urut tidak valid.")
        except:
            await update.message.reply_text("❌ Ketik angka urutan nomor.")
        return

    # ── Belum ada API key
    if not api_key:
        await update.message.reply_text("⚠️ Belum ada API Key. Klik tombol 👇", reply_markup=setup_keyboard())
        return

    # ── Menu utama
    if "Cek Saldo" in text:
        msg = await update.message.reply_text("⏳ Cek saldo...")
        bal = api_get_balance(api_key)
        if bal is not None:
            add_log(ctx, f"CEK SALDO ${bal:.4f}")
            await msg.edit_text(f"💰 *Saldo GrizzlySMS*\n\n*${bal:.4f}*\n\nTop up: grizzlysms.com", parse_mode="Markdown")
        else:
            await msg.edit_text("❌ Gagal cek saldo. Coba ganti API Key.")

    elif "Beli 1 Nomor" in text:
        await do_buy(update, ctx, 1)

    elif "Beli 3 Nomor" in text:
        await do_buy(update, ctx, 3)

    elif "Beli 5 Nomor" in text:
        await do_buy(update, ctx, 5)

    elif "Cek Harga" in text:
        msg = await update.message.reply_text("⏳ Ambil harga...")
        info = api_get_price(api_key, ctx.user_data["service"], ctx.user_data["country"])
        await msg.edit_text(
            f"💲 *Harga Saat Ini*\n\n"
            f"📦 {ctx.user_data['svc_name']} | {ctx.user_data['ctr_name']}\n"
            f"💰 Harga    : *${info['cost']}*\n"
            f"📊 Tersedia : *{info['count']}* nomor",
            parse_mode="Markdown"
        )

    elif text.startswith("📦"):
        await update.message.reply_text(
            f"📦 *Layanan Aktif*\n\n"
            f"Layanan : *{ctx.user_data['svc_name']}*\n"
            f"Negara  : *{ctx.user_data['ctr_name']}*\n\n"
            f"Ganti:\n`/setlayanan <kode_svc> <kode_negara> <nama_svc> <nama_negara>`\n\n"
            f"*Contoh:*\n"
            f"`/setlayanan wa 18 WhatsApp Vietnam`\n"
            f"`/setlayanan wa 6 WhatsApp Indonesia`\n"
            f"`/setlayanan tg 18 Telegram Vietnam`\n"
            f"`/setlayanan go 6 Google Indonesia`",
            parse_mode="Markdown"
        )

    elif text.startswith("🌍"):
        await update.message.reply_text(
            f"🌍 *Negara Aktif*: {ctx.user_data['ctr_name']}\n\n"
            f"Ganti negara dengan:\n"
            f"`/setlayanan <svc> <kode_negara> <nama_svc> <nama_negara>`\n\n"
            f"*Kode Negara Populer:*\n"
            f"`0`  = 🇺🇸 USA\n`6`  = 🇮🇩 Indonesia\n`18` = 🇻🇳 Vietnam\n"
            f"`22` = 🇬🇧 UK\n`32` = 🇮🇳 India\n`12` = 🇷🇺 Russia",
            parse_mode="Markdown"
        )

    elif "Ganti API Key" in text:
        ctx.user_data["waiting_for"] = "api_key_change"
        await update.message.reply_text(
            "🔑 Masukkan API Key baru:",
            reply_markup=ReplyKeyboardRemove()
        )

    elif text == "❌ Batalkan Nomor":
        actives = ctx.user_data.get("active_numbers", [])
        if not actives:
            await update.message.reply_text("ℹ️ Tidak ada nomor aktif."); return
        lines = ["📋 *Pilih nomor yang dibatalkan:*\n"]
        for i, n in enumerate(actives, 1):
            lines.append(f"{i}. `+{n['phone']}` | `{n['id']}`")
        lines.append("\n_Balas dengan angka (contoh: `1`)_")
        ctx.user_data["waiting_for"] = "cancel_select"
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    elif "Batalkan Semua" in text:
        actives = ctx.user_data.get("active_numbers", [])
        if not actives:
            await update.message.reply_text("ℹ️ Tidak ada nomor aktif."); return
        msg = await update.message.reply_text(f"⏳ Membatalkan {len(actives)} nomor...")
        success = 0
        for n in actives:
            AUTO_POLL_JOBS.pop(n["id"], None)
            if api_cancel(api_key, n["id"]):
                success += 1
                add_log(ctx, f"CANCEL ALL | {n['id']}")
            await asyncio.sleep(0.5)
        ctx.user_data["active_numbers"] = []
        await msg.edit_text(f"✅ Dibatalkan: {success}/{len(actives)} nomor")

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

# ─── SLASH COMMANDS ───────────────────────────────────────────────────────────

async def myid_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await update.message.reply_text(
        f"🆔 *Telegram ID kamu:* `{u.id}`\nNama: {u.first_name}\nUsername: @{u.username or '-'}",
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
        await update.message.reply_text("Gunakan: `/cancel <ID>`", parse_mode="Markdown"); return
    act_id = args[0].strip()
    msg = await update.message.reply_text(f"⏳ Membatalkan `{act_id}`...", parse_mode="Markdown")
    AUTO_POLL_JOBS.pop(act_id, None)
    if api_cancel(api_key, act_id):
        ctx.user_data["active_numbers"] = [n for n in ctx.user_data.get("active_numbers", []) if n["id"] != act_id]
        add_log(ctx, f"CANCEL CMD | {act_id}")
        await msg.edit_text(f"✅ `{act_id}` dibatalkan.", parse_mode="Markdown")
    else:
        await msg.edit_text(f"❌ Gagal batalkan `{act_id}`.", parse_mode="Markdown")

async def konfirmasi_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update): return
    ensure_init(ctx)
    api_key = get_api_key(ctx)
    if not api_key:
        await prompt_api_key(update, ctx); return
    args = ctx.args
    if not args:
        await update.message.reply_text("Gunakan: `/konfirmasi <ID>`", parse_mode="Markdown"); return
    act_id = args[0].strip()
    msg = await update.message.reply_text(f"⏳ Konfirmasi `{act_id}`...", parse_mode="Markdown")
    if api_confirm(api_key, act_id):
        ctx.user_data["active_numbers"] = [n for n in ctx.user_data.get("active_numbers", []) if n["id"] != act_id]
        add_log(ctx, f"KONFIRMASI | {act_id}")
        await msg.edit_text(f"✅ `{act_id}` dikonfirmasi!", parse_mode="Markdown")
    else:
        await msg.edit_text(f"❌ Gagal konfirmasi `{act_id}`.", parse_mode="Markdown")

async def setlayanan_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update): return
    ensure_init(ctx)
    api_key = get_api_key(ctx)
    if not api_key:
        await prompt_api_key(update, ctx); return
    args = ctx.args
    if len(args) < 4:
        await update.message.reply_text(
            "❌ Format:\n`/setlayanan <kode_svc> <kode_negara> <nama_svc> <nama_negara>`\n\n"
            "*Contoh:*\n"
            "`/setlayanan wa 18 WhatsApp Vietnam`\n"
            "`/setlayanan wa 6 WhatsApp Indonesia`\n"
            "`/setlayanan tg 18 Telegram Vietnam`\n"
            "`/setlayanan go 6 Google Indonesia`\n\n"
            "*Kode layanan:* `wa` `tg` `go` `fb` `ig` `tt`\n"
            "*Kode negara:* `0`=USA `6`=ID `18`=VN `22`=UK",
            parse_mode="Markdown"
        ); return

    svc_code, ctr_code = args[0], args[1]
    svc_name, ctr_name = args[2], args[3]
    msg = await update.message.reply_text("⏳ Cek harga...")
    info = api_get_price(api_key, svc_code, ctr_code)
    ctx.user_data.update({
        "service": svc_code, "country": ctr_code,
        "svc_name": svc_name, "ctr_name": ctr_name,
        "price": info["cost"]
    })
    add_log(ctx, f"SET LAYANAN | {svc_name} {ctr_name} ${info['cost']}")
    await msg.edit_text(
        f"✅ *Layanan diperbarui!*\n\n"
        f"📦 {svc_name} | {ctr_name}\n"
        f"💰 Harga: *${info['cost']}* | Tersedia: {info['count']}",
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

async def ceksms_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Manual cek SMS — biasanya tidak perlu karena sudah auto."""
    if not await check_access(update): return
    ensure_init(ctx)
    api_key = get_api_key(ctx)
    if not api_key:
        await prompt_api_key(update, ctx); return
    args = ctx.args
    if not args:
        actives = ctx.user_data.get("active_numbers", [])
        if not actives:
            await update.message.reply_text("ℹ️ Tidak ada nomor aktif.\n\nOTP sudah dikirim otomatis saat masuk."); return
        lines = ["📋 *ID Aktif (OTP sudah auto-notif):*\n"]
        for n in actives:
            lines.append(f"• `{n['id']}` → `+{n['phone']}`")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    act_id = args[0].strip()
    msg = await update.message.reply_text(f"⏳ Cek SMS `{act_id}`...", parse_mode="Markdown")
    result = api_get_sms(api_key, act_id)
    if result["status"] == "ok":
        await msg.edit_text(f"✅ OTP: `{result['code']}`\n\n`/konfirmasi {act_id}`", parse_mode="Markdown")
    elif result["status"] == "waiting":
        await msg.edit_text(f"⏳ Belum ada SMS untuk `{act_id}`. OTP akan notif otomatis.", parse_mode="Markdown")
    else:
        await msg.edit_text(f"❌ Status: {result.get('msg', '?')}", parse_mode="Markdown")

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",      start))
    app.add_handler(CommandHandler("myid",       myid_cmd))
    app.add_handler(CommandHandler("ceksms",     ceksms_cmd))
    app.add_handler(CommandHandler("cancel",     cancel_cmd))
    app.add_handler(CommandHandler("konfirmasi", konfirmasi_cmd))
    app.add_handler(CommandHandler("setlayanan", setlayanan_cmd))
    app.add_handler(CommandHandler("daftar",     daftar_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🐻 GrizzlySMS Bot v4 aktif!")
    print(f"Whitelist: {ALLOWED_IDS or 'Semua user'}")
    print("✅ Auto OTP aktif — OTP masuk langsung notif tanpa perintah")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
