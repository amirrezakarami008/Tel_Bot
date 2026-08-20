# ربات تلگرام — راهنمای راه‌اندازی

ربات production-ready با سه فیچر اصلی: لینک وبینار (gated)، پشتیبانی دوطرفه، و فایل‌های PDF هدیه (gated). همه تنظیمات فقط از فایل `.env` خوانده می‌شوند.

## پیش‌نیازها

- Docker و Docker Compose
- توکن بات از [@BotFather](https://t.me/BotFather)
- شناسه عددی تلگرام ادمین(ها) (مثلاً از `@userinfobot`)
- کانال(های) اجباری که **بات در آن‌ها ادمین** باشد (برای `get_chat_member`)

## ۱) پر کردن `.env`

```bash
cp .env.example .env
```

فایل `.env` را ویرایش کنید:

| متغیر | توضیح |
|--------|--------|
| `BOT_TOKEN` | توکن بات از BotFather |
| `ADMIN_TELEGRAM_IDS` | شناسه‌های عددی ادمین، جدا شده با کاما |
| `REQUIRED_CHANNELS` | کانال‌های اجباری (فرمت پایین) |
| `WEBINAR_LINK` | لینکی که بعد از تأیید عضویت ارسال می‌شود |
| `GIFT_FILES_DIR` | مسیر پوشه PDFها (در Docker معمولاً `./gift_files`) |
| `DATABASE_URL` | رشته اتصال async SQLAlchemy (باید با سرویس `db` هم‌خوان باشد) |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | اعتبار PostgreSQL |
| `LOG_LEVEL` | مثلاً `INFO` یا `DEBUG` |

### فرمت `REQUIRED_CHANNELS`

موارد با کاما جدا می‌شوند. فرمت‌های مجاز:

```env
# فقط یوزرنیم (ساده‌ترین حالت)
REQUIRED_CHANNELS=@channel_one,@channel_two

# آیدی عددی + یوزرنیم (برای چک عضویت با id و لینک عضویت با username)
REQUIRED_CHANNELS=-1001234567890:@mychannel,-1009876543210:@otherchannel

# فقط آیدی عددی (لینک عمومی دکمه ساخته نمی‌شود مگر username هم بدهید)
REQUIRED_CHANNELS=-1001234567890
```

### نمونه `DATABASE_URL` برای Docker Compose

اگر `POSTGRES_USER=botuser` و `POSTGRES_PASSWORD=secret` و `POSTGRES_DB=botdb` باشد:

```env
DATABASE_URL=postgresql+asyncpg://botuser:secret@db:5432/botdb
```

## ۲) قرار دادن فایل‌های هدیه

PDFهای هدیه را داخل پوشه `gift_files/` بگذارید:

```bash
cp /path/to/your.pdf ./gift_files/
```

این پوشه در Docker به‌صورت volume به کانتینر mount می‌شود.

## ۳) اجرا با Docker Compose

```bash
docker compose up -d --build
```

لاگ‌ها:

```bash
docker compose logs -f bot
```

توقف:

```bash
docker compose down
```

سرویس `bot` تا آماده شدن healthcheck دیتابیس صبر می‌کند؛ در `main.py` نیز اتصال دیتابیس با retry انجام می‌شود.

## ۴) اضافه / کم کردن کانال اجباری

1. `.env` را باز کنید و مقدار `REQUIRED_CHANNELS` را ویرایش کنید.
2. مطمئن شوید بات در کانال جدید **ادمین** است.
3. ربات را ری‌استارت کنید:

```bash
docker compose up -d --force-recreate bot
```

نیازی به تغییر کد نیست.

## ۵) دستورات ادمین

فقط برای شناسه‌های داخل `ADMIN_TELEGRAM_IDS`:

- `/stats` — تعداد کاربران، claim وبینار، claim هدیه، مکالمات پشتیبانی باز
- `/broadcast <متن>` — ارسال پیام به همه کاربران ثبت‌شده

پاسخ پشتیبانی: روی پیام اعلان بات (حاوی `#TICKET_...`) یا پیام فوروارد‌شده **ریپلای** کنید.

## ۶) دیپ‌لینک‌ها

- لینک وبینار: `https://t.me/YourBot?start=webinar`
- فایل هدیه: `https://t.me/YourBot?start=gift`

## ساختار پروژه

```
bot/
  config.py          # خواندن و اعتبارسنجی .env
  main.py            # entrypoint
  database/          # مدل‌ها و session async
  handlers/          # start, membership, webinar, gift, support, admin
  utils/             # keyboards و helpers
gift_files/          # PDFهای هدیه
docker-compose.yml
Dockerfile
.env.example
```

## عیب‌یابی سریع

- **خطا در بررسی عضویت:** بات را ادمین کانال کنید و `REQUIRED_CHANNELS` را درست تنظیم کنید.
- **فایل هدیه ارسال نمی‌شود:** وجود PDF در `gift_files/` و mount شدن volume را چک کنید.
- **ادمین پیام پشتیبانی نمی‌گیرد:** `ADMIN_TELEGRAM_IDS` را عددی و بدون فاصله اضافه بنویسید و حداقل یک‌بار `/start` با همان اکانت بزنید.
