# SQL Server Localize Translator

این ابزار جدول‌هایی را که نامشان به `Localize` یا `Localizes` ختم می‌شود و همچنین جدول خاص `dbo.Resource` را از طریق `sys.tables` و `sys.columns` پیدا می‌کند، رکوردهای زبان مبدا را می‌خواند و اگر برای همان شناسه جدول اصلی یا همان key زبان مقصد رکوردی وجود نداشته باشد، یک رکورد ترجمه‌شده insert می‌کند.

## فرض‌های اصلی

- ستون زبان با نام `LanguageId` وجود دارد.
- شناسه زبان‌ها:
  - `1`: فارسی (`fa`)
  - `2`: انگلیسی (`en`)
  - `3`: عربی (`ar`)
  - `4`: فرانسه (`fr`)
  - `5`: چینی (`zh`)
  - `6`: روسی (`ru`)
- ارتباط جدول لوکالایز با جدول اصلی ترجیحا از foreign key خوانده می‌شود. اگر FK تعریف نشده باشد، ابزار از نام جدول الگو می‌گیرد؛ مثلا برای `SiteMenuLocalize` ستون `SiteMenuId` را جست‌وجو می‌کند.
- جدول `dbo.Resource` یک special-case است: ستون `[Key]` به عنوان کلید تطبیق و ستون `Value` به عنوان متن قابل ترجمه استفاده می‌شود.
- ترجمه فقط روی ستون‌هایی انجام می‌شود که هم نوع متنی داشته باشند و هم نامشان در allow-list فایل `translator_app/translatable_columns.py` باشد.
- ستون‌های identity، computed و rowversion در insert وارد نمی‌شوند.

## ستون‌های قابل ترجمه

لیست ستون‌های قابل ترجمه در `translator_app/translatable_columns.py` نگهداری می‌شود. اگر یک ستون متنی داخل جدول لوکالایز باشد اما نامش در این لیست نباشد، ترجمه نمی‌شود و مقدارش در insert از رکورد مبدا کپی می‌شود. استثنا: در جدول `dbo.Resource` ستون `Value` ترجمه می‌شود.

اگر نام ستون `HTMLContent` باشد یا محتوای ستون شبیه HTML باشد، برای LibreTranslate مقدار `format=html` ارسال می‌شود. سایر متن‌ها با `format=text` ارسال می‌شوند.

## نصب

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

روی Windows:

```powershell
py -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

روی لینوکس باید ODBC Driver 18 for SQL Server هم نصب باشد.
پکیج `pyodbc` فقط wrapper پایتون است؛ خود درایور SQL Server با pip نصب نمی‌شود.

روی Windows باید Microsoft ODBC Driver 18 for SQL Server نصب باشد:

```powershell
winget install Microsoft.msodbcsql.18
```

## تنظیم اتصال

می‌توانید `.env.example` را به `.env` تبدیل کنید و مقادیر اتصال را تنظیم کنید، یا connection string کامل بدهید:

```bash
export SQLSERVER_CONNECTION_STRING='DRIVER={ODBC Driver 18 for SQL Server};SERVER=localhost;DATABASE=DbName;UID=sa;PWD=Password;Encrypt=yes;TrustServerCertificate=yes'
```

## اجرای dry-run

اجرای پیش‌فرض فقط گزارش می‌دهد و insert انجام نمی‌دهد:

```bash
.venv/bin/python main.py --source-language-id 1 --target-language-id 2
```

## اجرای GUI

برای اجرای نسخه گرافیکی، الان کافی است برنامه را بدون آرگومان اجرا کنید:

```bash
.venv/bin/python main.py
```

اجرای صریح GUI هم پشتیبانی می‌شود:

```bash
.venv/bin/python main.py --gui
```

روی Windows:

```powershell
.\.venv\Scripts\python main.py
.\.venv\Scripts\python main.py --gui
```

روی Windows معمولاً `tkinter` همراه Python نصب است. اگر روی لینوکس اجرا کردید و خطای `No module named 'tkinter'` گرفتید:

```bash
sudo apt install python3-tk
```

GUI سه تب دارد:

- `Connection`: تنظیم و ذخیره اتصال SQL Server، provider ترجمه و آدرس LibreTranslate
- `Operation`: انتخاب زبان مبدا/مقصد، اجرای همه جدول‌ها، اجرای یک جدول، یا تست تک جدول و سؤال برای ادامه
- `Resources`: ترجمه فایل‌های `.resx` مثل `Resources.resx`، `Messages.resx` و `Message.resx`
- `Logs`: نمایش زنده لاگ‌ها؛ فایل لاگ هر اجرا جداگانه در مسیر `LOG_DIR` ذخیره می‌شود

## ترجمه فایل‌های RESX

در تب `Resources` مسیر پوشه resource، زبان مبدا، زبان مقصد و فایل‌های موردنظر را انتخاب کنید. دکمه `Scan RESX` فقط تعداد کلیدها، کلیدهای موجود در مقصد و کلیدهای pending را گزارش می‌دهد. دکمه `Translate RESX` همان عملیات را اجرا می‌کند؛ اگر `Dry-run` روشن باشد فایل‌ها نوشته نمی‌شوند.

نام‌گذاری فایل‌ها مطابق پروژه‌های .NET انجام می‌شود:

- زبان انگلیسی (`en`) از فایل خنثی استفاده می‌کند: `Resources.resx` یا `Messages.resx`
- بقیه زبان‌ها از suffix زبان استفاده می‌کنند: `Resources.fa.resx`، `Resources.ar.resx`، `Resources.zh.resx`، `Messages.ru.resx`

اگر فایل مقصد وجود داشته باشد، کلیدهایی که از قبل مقدار دارند skip می‌شوند. اگر فایل مقصد وجود نداشته باشد، برنامه آن را از ساختار فایل مبدا می‌سازد، header/schema فایل RESX را نگه می‌دارد و فقط کلیدهای ترجمه‌شده را به آن اضافه می‌کند.

برای متن‌های HTML، همانند دیتابیس، مقدار `format=html` به LibreTranslate ارسال می‌شود؛ متن‌های عادی با `format=text` ترجمه می‌شوند.

## اجرای واقعی

```bash
.venv/bin/python main.py --source-language-id 1 --target-language-id 2 --execute
```

برای محدود کردن اجرا به یک جدول:

```bash
.venv/bin/python main.py --source-language-id 1 --target-language-id 2 --schema dbo --table SiteMenuLocalize --execute
```

## اجرای مرحله‌ای با یک جدول تست

اگر می‌خواهید ابتدا فقط یک جدول ترجمه شود و بعد از بررسی نتیجه سراغ همه جدول‌ها بروید:

```bash
.venv/bin/python main.py --source-language-id 1 --target-language-id 2 --test-table dbo.SiteMenuLocalize --execute
```

بعد از اتمام جدول تست، برنامه از شما می‌پرسد آیا همه جدول‌های `Localize/Localizes` اجرا شوند یا نه.

برای اجرای غیرتعاملی و ادامه خودکار بعد از جدول تست:

```bash
.venv/bin/python main.py --source-language-id 1 --target-language-id 2 --test-table dbo.SiteMenuLocalize --continue-after-test --execute
```

## مترجم رایگان

در CLI اگر provider تنظیم نکنید، `google-free` استفاده می‌شود. در GUI و `.env.example` مقدار پیش‌فرض روی `libretranslate` گذاشته شده تا بتوانید آدرس سرویس LibreTranslate خودتان را تنظیم کنید.

Provider `google-free` از endpoint عمومی و بدون API key گوگل استفاده می‌کند، اما قرارداد رسمی Google Cloud نیست و ممکن است rate limit یا تغییر رفتار داشته باشد.

اگر ترجمه کاملا رایگان، قابل کنترل و پایدارتر می‌خواهید، LibreTranslate را self-host کنید و اجرا را این‌طور انجام دهید:

```bash
.venv/bin/python main.py --provider libretranslate --libretranslate-url http://localhost:5000 --source-language-id 1 --target-language-id 2 --execute
```

اگر LibreTranslate روی سرور جدا و پشت IIS است، مقدار `--libretranslate-url` را URL همان سایت IIS بگذارید:

```bash
.venv/bin/python main.py --provider libretranslate --libretranslate-url https://translate.example.com --source-language-id 1 --target-language-id 2 --execute
```

## لاگ و خطایابی

همه لاگ‌ها همزمان در کنسول و فایل `logs/translator_YYYYMMDD_HHMMSS.log` ذخیره می‌شوند. لاگ شامل جدول‌های پیدا شده، ستون‌های متنی، تعداد رکوردهای pending، درصد پیشرفت، retryها و exception کامل هر رکورد خطادار است.

## مثال SiteMenu

اگر `SiteMenu` رکوردی با شناسه `1` داشته باشد و `SiteMenuLocalize` یک رکورد فارسی برای `SiteMenuId = 1` داشته باشد، اجرای فارسی به انگلیسی بررسی می‌کند آیا رکوردی با `SiteMenuId = 1` و `LanguageId = 2` وجود دارد یا نه. اگر وجود داشته باشد skip می‌شود؛ اگر وجود نداشته باشد، متن ستون‌های متنی ترجمه و یک رکورد جدید با `LanguageId = 2` insert می‌شود.
