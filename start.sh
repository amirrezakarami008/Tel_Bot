#!/usr/bin/env bash
# بالا آوردن کل استک: PostgreSQL + ربات تلگرام
set -euo pipefail

cd "$(dirname "$0")"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}✔${NC} $*"; }
warn()  { echo -e "${YELLOW}!${NC} $*"; }
fail()  { echo -e "${RED}✖${NC} $*"; exit 1; }

# --- پیش‌نیازها ---
if ! command -v docker >/dev/null 2>&1; then
  fail "Docker نصب نیست. اول نصب کن: sudo apt install -y docker.io docker-compose-v2"
fi

if ! docker compose version >/dev/null 2>&1; then
  fail "دستور «docker compose» در دسترس نیست. پکیج docker-compose-v2 را نصب کن."
fi

if [[ ! -f .env ]]; then
  fail "فایل .env پیدا نشد. اول: cp .env.example .env و مقادیر را پر کن."
fi

# بررسی فیلدهای ضروری در .env (بدون چاپ مقدار)
required_vars=(BOT_TOKEN ADMIN_TELEGRAM_IDS REQUIRED_CHANNELS WEBINAR_LINK POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB DATABASE_URL)
missing=()
for var in "${required_vars[@]}"; do
  # shellcheck disable=SC1091
  value="$(grep -E "^${var}=" .env | head -n1 | cut -d= -f2- | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  # حذف کامنت این‌لاین احتمالی
  value="${value%%#*}"
  value="$(echo "$value" | xargs 2>/dev/null || true)"
  if [[ -z "$value" ]]; then
    missing+=("$var")
  fi
done

if ((${#missing[@]} > 0)); then
  fail "این متغیرها در .env خالی‌اند: ${missing[*]}"
fi

mkdir -p gift_files

echo
info "در حال بیلد و اجرای سرویس‌ها (db + bot)..."
docker compose up --build -d

echo
info "وضعیت کانتینرها:"
docker compose ps

echo
info "همه‌چیز بالا آمد."
echo "  لاگ ربات:     docker compose logs -f bot"
echo "  توقف:         docker compose down"
echo "  ری‌استارت:    docker compose restart"
echo
warn "اگر اولین بار است، چند ثانیه صبر کن تا Postgres healthy شود و ربات وصل شود."
