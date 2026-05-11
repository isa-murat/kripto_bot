# Deployment

Bot iki şekilde çalıştırılabilir:

1. **Lokal** (uv ile, sanal ortam) — geliştirme için
2. **Docker** (lokal veya VPS) — uzun süreli çalıştırma için (önerilen)

## 1. Docker — lokal

### Ön koşul

- **Windows:** Docker Desktop kurulu olmalı (https://www.docker.com/products/docker-desktop/)
- **Linux:** `docker` + `docker compose` (Docker Engine 20.10+)

Doğrula:
```powershell
docker --version
docker compose version
```

### `.env` hazırla

```powershell
Copy-Item .env.example .env
notepad .env
# BINANCE_API_KEY, BINANCE_API_SECRET, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID doldur
```

### Build + run

```powershell
docker compose build
docker compose up -d        # detached
docker compose logs -f      # canlı log takip
```

`-d` ile arka planda çalışır, terminali kapatabilirsin. Bot çökerse veya Docker
restart edilirse `restart: unless-stopped` policy'siyle otomatik kalkar.

### Durum + komutlar

```powershell
docker compose ps                 # container durumu
docker compose logs --tail 100    # son 100 satır log
docker compose restart            # restart
docker compose stop               # durdur (data kaybetmez)
docker compose down               # durdur + container sil (volume'lar durur)
docker compose down -v            # her şeyi sil (volume dahil) — DİKKAT
```

### Tek seferlik komutlar (downloader, test, vs.)

```powershell
# Tarihsel veri indir (container içinde, host'taki data/ klasörüne yazar)
docker compose run --rm kripto-bot python -m src.data.downloader all --from 2025-11-01

# Pytest çalıştır
docker compose run --rm kripto-bot pytest

# Telegram smoke test
docker compose run --rm kripto-bot python -m scripts.test_telegram

# Konfig sanity check
docker compose run --rm kripto-bot python -m src.main check
```

### Config değişikliği

`config/settings.yaml` veya `config/strategy_params.yaml`'ı değiştir, sonra:

```powershell
docker compose restart
```

(Volume read-only mount edildiği için container içinden değiştirilemez — host'tan
düzenle.)

### Kod değişikliği — iki yol

**A) Geliştirme: hot reload (önerilen, tek komut yeter)**

```powershell
docker compose watch
```

`docker-compose.yml`'daki `develop.watch` bloğu devreye girer:
- `src/`, `config/`, `scripts/` değişince → dosya container'a sync edilir + Python süreci restart olur (1-2 sn)
- `pyproject.toml` veya `docker/Dockerfile` değişince → image baştan build edilir
- `__pycache__` ve `*.pyc` dosyaları ignore edilir, gereksiz restart olmaz

Foreground çalışır, Ctrl+C ile durur. Detached istiyorsan: `docker compose up --watch -d`.

**B) Production-style: manuel rebuild**

```powershell
docker compose up -d --build
```

İmage yeniden build edilir, eski container yerine yenisi başlar, volume'lar korunur.

## 2. VPS — Hetzner örneği (ileride)

### Ön koşul

- Ubuntu 22.04+ VPS (Hetzner CX11 ~€4/ay, Frankfurt veya Helsinki — Türkiye'ye latency 30-50ms)
- SSH erişimi
- Domain ZORUNLU değil

### Kurulum

```bash
# VPS'te
ssh root@<vps-ip>

# Docker kur
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker

# Repo'yu çek
apt install -y git
git clone <repo-url> kripto_bot
cd kripto_bot

# .env doldur
cp .env.example .env
nano .env

# Build + run
docker compose up -d
docker compose logs -f
```

### Sistem boyunca otomatik başlatma

Docker zaten boot'ta başlar (`systemctl enable docker`). `restart: unless-stopped`
policy bot'u otomatik ayağa kaldırır. Ekstra systemd unit gerekmez.

### Log monitoring

```bash
# Canlı
docker compose logs -f --tail=50

# Disk üzerindeki dosya logları (loguru)
tail -f logs/bot.log
```

Docker JSON log driver max 10 MB × 5 dosya = 50 MB, kendiliğinden döner. Loguru
ayrıca `logs/bot.log`'a yazıyor (config'de `rotation: "100 MB"`, `retention: "14 days"`).

### Backup

Önemli dosyalar:
- `data/` — Parquet OHLCV cache + SQLite trade log
- `logs/` — bot logs
- `.env` — sırlar (asla commit edilmez)

Basit cron backup:
```bash
0 4 * * * tar -czf /backups/kripto_$(date +\%Y\%m\%d).tar.gz /opt/kripto_bot/data /opt/kripto_bot/.env
```

### Update flow

```bash
cd kripto_bot
git pull
docker compose up -d --build
```

İmage yeniden build edilir, eski container yerine yenisi başlar, volume'lar korunur.

## Sorun giderme

| Sorun | Çözüm |
|---|---|
| `Cannot connect to the Docker daemon` | Docker Desktop açık değil veya servis durdu — başlat |
| Container hemen çıkıyor | `docker compose logs` ile sebebi bak — genelde `.env` eksik |
| Telegram mesaj gelmiyor | `docker compose run --rm kripto-bot python -m scripts.test_telegram` |
| WS hatası (lokal) | ADR-0009 — bu IP'de Binance Futures WS kapalı, REST polling kullanılıyor |
| Disk doluyor | `docker system prune -a` (kullanılmayan image/container/cache temizler) |
| Bar log gelmiyor | Network kontrolü: `docker compose run --rm kripto-bot python -m src.main check` |
