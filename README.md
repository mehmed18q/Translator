# SQL Server Localize Translator

این ابزار جدول‌هایی را که نامشان به `Localize` یا `Localizes` ختم می‌شود از طریق `sys.tables` و `sys.columns` پیدا می‌کند، رکوردهای زبان مبدا را می‌خواند و اگر برای همان شناسه جدول اصلی و زبان مقصد رکوردی وجود نداشته باشد، یک رکورد ترجمه‌شده insert می‌کند.

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
- ستون‌های متنی شامل `nvarchar`, `varchar`, `nchar`, `char`, `text`, `ntext` ترجمه می‌شوند.
- ستون‌های identity، computed و rowversion در insert وارد نمی‌شوند.

## نصب

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

روی لینوکس باید ODBC Driver 18 for SQL Server هم نصب باشد.

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

Provider پیش‌فرض `google-free` است. این provider از endpoint عمومی و بدون API key گوگل استفاده می‌کند، اما قرارداد رسمی Google Cloud نیست و ممکن است rate limit یا تغییر رفتار داشته باشد.

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
