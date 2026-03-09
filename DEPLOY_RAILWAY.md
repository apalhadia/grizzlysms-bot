# 🐻 Deploy GrizzlySMS Bot ke Railway

## Persiapan File
Pastikan kamu punya semua file ini:
- `grizzlysms_bot_v3.py` ← bot utama
- `requirements.txt`
- `Procfile`
- `runtime.txt`

---

## Langkah Deploy ke Railway

### 1. Daftar Railway
- Buka: https://railway.app
- Klik **Login with GitHub**
- Kalau belum punya GitHub, daftar dulu di github.com (gratis)

### 2. Buat Project Baru
- Klik **New Project**
- Pilih **Deploy from GitHub repo** ATAU **Empty Project**
- Kalau pilih Empty Project → klik **Add Service** → **Empty Service**

### 3. Upload File Bot
Di halaman service, klik tab **Settings** lalu cari **Source**:

**Cara termudah - pakai GitHub:**
1. Buat repo baru di github.com
2. Upload semua 4 file ke repo tersebut
3. Di Railway → Connect GitHub repo

**Atau pakai Railway CLI:**
```bash
npm install -g @railway/cli
railway login
railway init
railway up
```

### 4. Set Environment Variables
Di Railway → tab **Variables** → tambahkan:
```
BOT_TOKEN = token_dari_botfather_kamu
```

### 5. Deploy!
Railway otomatis detect `Procfile` dan jalankan bot.
Lihat log di tab **Logs** — kalau muncul:
```
🐻 GrizzlySMS Bot v3 aktif!
```
Berarti bot sudah online 24 jam! ✅

---

## Cara Paling Cepat (Drag & Drop via GitHub)

1. Daftar https://github.com
2. Buat repo baru → klik **+** → **New repository**
3. Nama repo: `grizzlysms-bot` → klik **Create**
4. Upload semua file (drag & drop)
5. Commit changes
6. Ke railway.app → New Project → Deploy from GitHub
7. Pilih repo `grizzlysms-bot`
8. Tambah variable `BOT_TOKEN`
9. Deploy otomatis! 🚀
