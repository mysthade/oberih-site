# Сайт КЗ «НРЦ «Оберіг»» (м. Івано-Франківськ)

Офіційний вебсайт комунального закладу «Навчально-реабілітаційний центр „Оберіг“» Івано-Франківської міської ради.

- **Адреса:** вул. Гната Хоткевича, 52-А, м. Івано-Франківськ, 76020
- **Телефон:** (0342) 55-05-24
- **E-mail:** nrts.oberih@osvita.if.ua
- **Стек:** Python 3.12+, Django 5.2 LTS, PostgreSQL, WhiteNoise, Gunicorn (без Docker)

---

## Структура проєкту

```text
oberih-site-main/
├── config/                 # Конфігурація Django-проєкту
│   ├── admin_url.py        # Перевірка секретної адреси ADMIN_URL
│   ├── asgi.py
│   ├── settings.py         # Налаштування з підтримкою .env та продакшн-безпекою
│   ├── urls.py             # Маршрути проєкту (керування сайтом на /<ADMIN_URL>, обробники 400/403/404/500)
│   └── wsgi.py
├── core/                   # Основний застосунок сайту
│   ├── management/commands/
│   │   ├── backup_site.py      # Резервне копіювання БД (pg_dump) та папки media/
│   │   ├── import_home.py      # Початковий імпорт головної сторінки та новин
│   │   └── import_site_map.py  # Початковий імпорт розділів і внутрішніх сторінок
│   ├── migrations/
│   ├── admin.py            # Керування сайтом українською мовою
│   ├── context_processors.py
│   ├── middleware.py       # CSP та обмеження спроб входу в керування сайтом
│   ├── models.py           # Моделі даних і валідація файлів (PDF, JPG/PNG)
│   ├── storage.py          # Сховище статики WhiteNoise (manifest_strict = False)
│   ├── tests.py            # Автоматичні тести (PostgreSQL)
│   ├── urls.py             # Маршрути публічної частини, 301-редіректи, robots.txt, sitemap.xml
│   └── views.py            # Представлення сторінок, форми контактів та сторінок помилок
├── deploy/                 # Приклади конфігурацій для сервера
│   ├── backup.cron         # Приклад розкладу cron для бекапів та очищення сесій
│   ├── gunicorn.conf.py    # Конфігурація Gunicorn (VPS та PaaS)
│   ├── nginx-oberih.conf   # Конфігурація Nginx (HTTPS, статика, захищений /media/)
│   └── oberih.service      # Systemd-юніт для автозапуску Gunicorn
├── static/                 # Статичні файли фронтенду (css, fonts, img, js)
├── templates/              # Шаблони Django (base, home, section, page, news, contacts, 400/403/404/429/500)
├── tools/                  # Допоміжні інструменти (site_map.py, compare_html.py)
├── .env.example            # Шаблон змінних середовища з поясненнями
├── manage.py
├── requirements.txt        # Зафіксовані версії залежностей (==)
└── README.md
```

---

## 1. Як запустити локально для розробки

### Крок 1. Підготовка бази даних PostgreSQL

Створіть базу даних і користувача в PostgreSQL (право `CREATEDB` потрібне для запуску автоматичних тестів `python manage.py test`):

```sql
CREATE DATABASE oberih_db ENCODING 'UTF8';
CREATE USER oberih_user WITH PASSWORD 'ваш_надійний_пароль' CREATEDB;
GRANT ALL PRIVILEGES ON DATABASE oberih_db TO oberih_user;
ALTER DATABASE oberih_db OWNER TO oberih_user;
```

### Крок 2. Віртуальне середовище та залежності

**Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m django --version
```

**Linux / macOS:**
```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m django --version
```

### Крок 3. Налаштування `.env`

Скопіюйте `.env.example` у `.env` та вкажіть локальні параметри:

```ini
DEBUG=True
SECRET_KEY=згенерований-довгий-секретний-ключ
ALLOWED_HOSTS=127.0.0.1,localhost
SITE_URL=http://127.0.0.1:8000
ADMIN_URL=<згенерований-випадковий-рядок-мінімум-16-символів>/
DB_NAME=oberih_db
DB_USER=oberih_user
DB_PASSWORD=ваш_надійний_пароль
DB_HOST=127.0.0.1
DB_PORT=5432
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
SESSION_COOKIE_SECURE=False
CSRF_COOKIE_SECURE=False
USE_X_FORWARDED_PROTO=False
SECURE_SSL_REDIRECT=False
SECURE_HSTS_SECONDS=0
```

Згенерувати новий `SECRET_KEY`:
```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

### Крок 4. Міграції, таблиця кешу, початкові дані та запуск

```bash
python manage.py migrate
python manage.py createcachetable
python manage.py import_site_map
python manage.py import_home
python manage.py createsuperuser
python manage.py runserver
```

- Публічний сайт: `http://127.0.0.1:8000/`
- Керування сайтом: `http://127.0.0.1:8000/<ADMIN_URL>` (видима назва задається через `ADMIN_SITE_NAME` у `.env`, за замовчуванням «Керування сайтом»)

---

## 2. Розгортання на сервері (VPS або PaaS)

### Особливості розгортання на VPS (Ubuntu/Debian + Nginx + Gunicorn + PostgreSQL)

1. **Системні пакети та користувач:**
   Встановіть `python3.12`, `python3.12-venv`, `postgresql`, `postgresql-client`, `nginx`. Створіть непривілейованого користувача (наприклад, `oberih`) і клонуйте репозиторій у `/var/www/oberih-site`.
2. **Файл `.env` на бойовому сервері (права `chmod 600 .env`):**
   - `DEBUG=False`
   - `ALLOWED_HOSTS=ваш-домен.if.ua,www.ваш-домен.if.ua`
   - `CSRF_TRUSTED_ORIGINS=https://ваш-домен.if.ua,https://www.ваш-домен.if.ua`
   - `SITE_URL=https://ваш-домен.if.ua`
   - **`USE_X_FORWARDED_PROTO=True`** — **критично важливо за Nginx та на PaaS!** Nginx приймає HTTPS-з'єднання і передає запит у Gunicorn по локальному HTTP із заголовком `X-Forwarded-Proto: https`. Якщо залишити `USE_X_FORWARDED_PROTO=False`, то при `SECURE_SSL_REDIRECT=True` Django вважатиме кожен запит незахищеним і виникне **нескінченний цикл перенаправлень (redirect loop)**.
   - **`TRUST_X_FORWARDED_FOR=True`** (при порожньому `CLIENT_IP_HEADER=`) — вмикайте на VPS за власним Nginx, який передає `X-Forwarded-For`, щоб ліміт форми звернень і ліміт входу в керування сайтом визначали реальну адресу клієнта (береться останній запис із заголовка), а не `127.0.0.1`.
   - **`SERVE_MEDIA=False`** — на VPS залишайте `False`, оскільки `/media/` роздає безпосередньо Nginx за `deploy/nginx-oberih.conf`.
   - **`SECURE_HSTS_SECONDS`**: під час першого запуску залиште `3600` (1 година), переконайтеся, що HTTPS-сертифікат працює бездоганно, і лише після цього збільште до `31536000` (1 рік).
3. **Підготовка бази, кешу, статики та початкового вмісту:**
   ```bash
   source .venv/bin/activate
   pip install -r requirements.txt
   python manage.py migrate --noinput
   python manage.py createcachetable
   python manage.py collectstatic --noinput
   python manage.py import_site_map
   python manage.py import_home
   python manage.py createsuperuser
   python manage.py check --deploy
   ```
4. **Запуск служб systemd та Nginx:**
   - Скопіюйте `deploy/oberih.service` у `/etc/systemd/system/oberih.service`, відредагуйте шляхи та виконайте `sudo systemctl enable --now oberih`.
   - Скопіюйте `deploy/nginx-oberih.conf` у `/etc/nginx/sites-available/oberih.conf`, вкажіть свій домен і сертифікати Let's Encrypt, увімкніть сайт та виконайте `sudo nginx -t && sudo systemctl reload nginx`.
   - Налаштуйте щоденні резервні копії за прикладом `deploy/backup.cron`.

### Особливості розгортання на PaaS (Render, Railway, Fly.io, Heroku тощо)

1. **Команда збирання (Build Command):**
   ```bash
   pip install -r requirements.txt && python manage.py collectstatic --noinput && python manage.py migrate --noinput && python manage.py createcachetable
   ```
2. **Команда запуску (Start Command):**
   ```bash
   gunicorn config.wsgi:application --config deploy/gunicorn.conf.py
   ```
   *(Файл `deploy/gunicorn.conf.py` автоматично підхоплює змінну середовища `$PORT`, яку передає PaaS, і слухає `0.0.0.0:$PORT`; використовує потоковий клас `worker_class="gthread"` із `GUNICORN_THREADS=4` за замовчуванням, щоб віддача великих PDF не блокувала воркер. Враховуйте, що максимальна кількість одночасних з'єднань із базою даних дорівнює **воркери × потоки** (`workers × threads`)).*
3. **Збереження та роздача завантажених файлів (`media/`) на PaaS:**
   - Файлова система контейнерів на PaaS є тимчасовою (ефемерною) і очищується при кожному перезапуску чи оновленні. Щоб завантажені PDF-документи та фотографії не зникали, **обов'язково підключіть постійний диск (Persistent Volume / Disk)** і вкажіть шлях до нього у змінній `MEDIA_ROOT` у `.env`.
   - Оскільки на PaaS (Render) немає окремого Nginx для віддачі медіафайлів з диска, обов'язково увімкніть **`SERVE_MEDIA=True`** у `.env` (тоді Django безпечно роздаватиме `/media/` із перевіркою дозволених розширень `pdf/jpg/jpeg/png`, захистом від виходу за межі `MEDIA_ROOT` і заголовками `nosniff` та `Content-Security-Policy: default-src 'none'; sandbox`).
4. **Змінні середовища на PaaS (зокрема Render):**
   - Увімкніть `USE_X_FORWARDED_PROTO=True` та `SERVE_MEDIA=True`.
   - Для визначення реальної IP-адреси клієнта на **Render** встановіть `CLIENT_IP_HEADER=HTTP_TRUE_CLIENT_IP`, а `TRUST_X_FORWARDED_FOR=False` (на VPS за власним Nginx навпаки: `CLIENT_IP_HEADER=` залишається порожнім, `TRUST_X_FORWARDED_FOR=True`, `SERVE_MEDIA=False`).

---

## 3. Що робити при зміні домену

Якщо заклад отримує новий домен (наприклад, `oberih.if.ua`), код змінювати **не потрібно**:

1. У файлі `.env` оновіть три змінні:
   ```ini
   ALLOWED_HOSTS=oberih.if.ua,www.oberih.if.ua
   CSRF_TRUSTED_ORIGINS=https://oberih.if.ua,https://www.oberih.if.ua
   SITE_URL=https://oberih.if.ua
   ```
2. У конфігурації Nginx (`/etc/nginx/sites-available/oberih.conf`) замініть `server_name` і шляхи до SSL-сертифіката нового домену.
3. Перезапустіть Gunicorn і Nginx:
   ```bash
   sudo systemctl restart oberih
   sudo nginx -t && sudo systemctl reload nginx
   ```
   Після цього канонічні посилання, OpenGraph, `robots.txt`, `sitemap.xml` та мікророзмітка JSON-LD автоматично використовуватимуть новий домен.

### Зміна адреси керування сайтом (`ADMIN_URL`)

Адреса входу до керування сайтом задається виключно через змінну середовища `ADMIN_URL` у файлі `.env` (без значення за замовчуванням у коді):

1. Згенеруйте випадковий рядок (лише латинські літери, цифри та дефіс, щонайменше 16 символів до кінцевого слеша `/`, без слів `admin`, `login`, `panel`, `manage`, `cms`, `django` тощо):
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(16).replace('_','-') + '/')"
   ```
2. Запишіть отримане значення у файл `.env` на сервері:
   ```ini
   ADMIN_URL=<згенероване-значення>/
   ```
3. Перезапустіть застосунок (`sudo systemctl reload oberih` на VPS або перезапуск сервісу на PaaS).
4. Передайте нову адресу редакторам сайту окремим захищеним каналом зв'язку (не публікуйте її у відкритому репозиторії чи на сторінках сайту).

---

## 4. Резервне копіювання та відновлення

### Створення резервної копії (`backup_site`)

Команда створює стиснений дамп бази даних PostgreSQL (`pg_dump -Fc`) та архів папки `media/` із правами доступу `600` і залишає останні `BACKUP_KEEP_COUNT` (за замовчуванням 7) копій:

```bash
python manage.py backup_site
```

- Каталог `BACKUP_DIR` **обов'язково має знаходитися за межами каталогу проєкту (git) та за межами `MEDIA_ROOT`** (наприклад, `/var/backups/oberih` або `../oberih_backups`). Якщо вказати каталог всередині проєкту чи `media/`, команда відмовиться працювати й пояснить причину.
- Пароль БД передається до `pg_dump` виключно через змінну середовища процесу (`PGPASSWORD`), тому не світиться у списку процесів ОС.
- Під час ротації видаляються лише файли, що відповідають шаблонам `oberih_db_YYYYMMDD_HHMMSS.dump` та `oberih_media_YYYYMMDD_HHMMSS.tar.gz`.

### Як відновити сайт із резервної копії та перевірити відновлення

1. **Відновлення бази даних PostgreSQL:**
   ```bash
   PGPASSWORD="ваш_пароль_бд" pg_restore --clean --if-exists --no-owner -h 127.0.0.1 -p 5432 -U oberih_user -d oberih_db /шлях/до/oberih_db_YYYYMMDD_HHMMSS.dump
   ```
2. **Відновлення папки `media/`:**
   ```bash
   tar -xzf /шлях/до/oberih_media_YYYYMMDD_HHMMSS.tar.gz -C /var/www/oberih-site/
   ```
3. **Як перевірити справність резервної копії (рекомендовано раз на квартал):**
   - Створіть тимчасову тестову базу: `createdb -h 127.0.0.1 -U oberih_user oberih_restore_test`
   - Відновіть у неї свіжий `.dump`-файл: `pg_restore -h 127.0.0.1 -U oberih_user -d oberih_restore_test oberih_db_*.dump`
   - Перевірте наявність таблиць і записів: `psql -h 127.0.0.1 -U oberih_user -d oberih_restore_test -c "SELECT count(*) FROM core_page;"`
   - Перевірте цілісність архіву медіафайлів: `tar -tzf oberih_media_*.tar.gz > /dev/null && echo "Архів цілий"`
   - Видаліть тимчасову базу: `dropdb -h 127.0.0.1 -U oberih_user oberih_restore_test`

---

## 5. Що НЕ запускати на працюючому сайті

- **`python manage.py import_site_map`** та **`python manage.py import_home`** — це команди **лише для першого початкового наповнення порожньої бази даних**.
  - Обидві команди мають вбудований запобіжник: якщо в базі вже є сторінки або новини, вони зупиняються без жодних змін і просять підтвердження ключем `--yes`.
  - Не запускайте їх з ключем `--yes` на працюючому сайті, де редактори вже вносили зміни, оскільки це може повторно створити видалені стартові чернетки новин або відновити початкові заглушки на сторінках, звідки редактор навмисно прибрав усі блоки.
- **`python manage.py flush`** — повністю очищує базу даних. Ніколи не запускайте на бойовому сервері.

---

## 6. Як оновити код на сервері без простою

```bash
cd /var/www/oberih-site
source .venv/bin/activate
git pull origin main
pip install -r requirements.txt
python manage.py migrate --noinput
python manage.py collectstatic --noinput
python manage.py check --deploy
sudo systemctl reload oberih
```
*(Команда `systemctl reload oberih` надсилає Gunicorn сигнал `HUP`, який плавно перезапускає робочі процеси без обриву поточних запитів відвідувачів).*

---

## 7. Чекліст перевірки після розгортання

- [ ] Перевірити версію Django та залежності:
  ```bash
  python -m django --version
  pip install -r requirements.txt
  ```
- [ ] Перевірити відсутність відомих вразливостей у пакетах через `pip-audit` (встановлюється разово в систему або окреме середовище, не додаючи його у `requirements.txt`):
  ```bash
  pipx run pip-audit -r requirements.txt
  ```
- [ ] Запустити перевірку безпеки Django (має бути `0 issues`):
  ```bash
  python manage.py check --deploy
  ```
- [ ] Запустити автоматичні тести на тестовій БД:
  ```bash
  python manage.py test -v 2
  ```
- [ ] Відкрити у браузері по HTTPS і перевірити:
  - [ ] Головну сторінку (`/`) — слайдер новин, панель доступності (A / A+ / A++ / Контраст / Темна тема), документи, спонсори.
  - [ ] Сторінку розділу (`/pro-nas/`) та внутрішню сторінку (`/pro-nas/struktura-ta-orhany-upravlinnia/`), завантаження PDF-документа з `/media/`.
  - [ ] Список новин (`/novyny/`) та окрему опубліковану новину.
  - [ ] Сторінку контактів (`/kontakty/`) та тестове надсилання форми «Поставити запитання» (перевірити появу звернення в `/<ADMIN_URL>core/contactmessage/`).
  - [ ] Вхід у керування сайтом (`/<ADMIN_URL>`) та захист від підбору пароля.
  - [ ] Неіснуючу адресу (`/neisnuiucha-storinka/`) — має відкритися сторінка `404` у дизайні сайту зі статусом `404`.
  - [ ] Службові файли `/robots.txt` та `/sitemap.xml` (мають містити актуальний `SITE_URL` з `https://`).
  - [ ] Створити першу резервну копію: `python manage.py backup_site`.

---

## 8. Посібник для редакторів сайту

### Як вибрати сторінку політики конфіденційності

Щоб біля галочки згоди на обробку персональних даних у формі «Поставити запитання» (на головній сторінці `/` та на сторінці `/kontakty/`) з'явилося посилання на сторінку політики конфіденційності:

1. Увійдіть у керування сайтом (`/<ADMIN_URL>`), відкрийте розділ **«Сторінки сайту»** та натисніть **«Додати сторінку сайту»**.
2. Оберіть відповідний розділ (наприклад, «Офіційна сторінка»), вкажіть назву (наприклад, «Політика конфіденційності» або «Порядок обробки персональних даних»), перевірте, що стоїть галочка **«Опубліковано»**, додайте затверджений замовником текст або PDF-документ і збережіть сторінку.
3. Перейдіть у розділ **«Налаштування сайту»** (`/<ADMIN_URL>core/sitesettings/`).
4. У полі **«Сторінка політики конфіденційності»** виберіть створену сторінку зі списку й натисніть **«Зберегти»**.
   - Якщо сторінку вибрано і вона опублікована — у тексті згоди з'явиться активне посилання `обробку персональних даних` (відкривається в новій вкладці).
   - Якщо поле залишити порожнім або зняти зі сторінки галочку «Опубліковано» — виводиться звичайний текст згоди без посилання.

### Що замінити перед запуском

Після початкового імпорту (`import_site_map`) частина сторінок містить тексти-заглушки з макета та порожні слоти для офіційних PDF-документів. Перед відкриттям сайту для відвідувачів виконайте три кроки:

1. **Знайдіть і замініть тексти-заглушки на сторінках:**
   - Відкрийте **«Сторінки сайту»** (`/<ADMIN_URL>core/page/`).
   - У правій панелі фільтрів натисніть **«Містить заглушку» → «Є заглушка»** (також дивіться колонку **«Містить заглушку»** зі значенням **«Так»**).
   - Відкрийте кожну таку сторінку, розгорніть блок **«1. Текстові блоки, списки та таблиці сторінки»** і:
     - видаліть технічну плашку-нагадування (`Важлива примітка в рамці (notice)` із текстом *«Зміст сторінки надасть адміністрація...»*), поставивши галочку **«Видалити»** праворуч від блоку, або замініть її на справжній текст;
     - замініть демонстраційні тексти, списки та таблиці з позначкою `(приклад)` на фактичні дані закладу та натисніть **«Зберегти»**.
2. **Завантажте PDF-файли офіційних документів:**
   - У списку **«Сторінки сайту»** колонка **«Документів без файлу»** показує кількість документів на сторінці, до яких ще не прикріплено PDF-файл (на сайті такі документи тимчасово відображаються як неклікабельні картки з підписом *«Документ готується до оприлюднення»*).
   - Також ви можете відкрити розділ **«Документи»** (`/<ADMIN_URL>core/document/`) і відфільтрувати список за фільтром **«Наявність файлу» → «Без файлу»**.
   - Відкрийте документ (або блок **«2. Офіційні документи (PDF) на цій сторінці»** всередині сторінки), виберіть PDF-файл у полі **«PDF-файл»** і збережіть зміни — на сайті одразу з'явиться активне посилання для завантаження PDF.
3. **УВАГА: сторінки, які НЕ слід знімати з публікації або змінювати їм адресу (`slug`)**:
   На головній сторінці сайту (`templates/home.html`) розміщені прямі швидкі посилання на ключові сторінки закладу. Не знімайте галочку **«Опубліковано»** і не видаляйте ці **13 внутрішніх сторінок**, щоб посилання з головної сторінки не вели на помилку `404`:
   - `/ofitsiina-storinka/statut/` — Статут
   - `/ofitsiina-storinka/litsenziia/` — Ліцензія
   - `/ofitsiina-storinka/pravyla-pryiomu/` — Правила прийому до закладу освіти
   - `/prozorist-ta-vidkrytist/finansova-zvitnist/` — Фінансова звітність
   - `/prozorist-ta-vidkrytist/richnyi-zvit/` — Річний звіт
   - `/prozorist-ta-vidkrytist/dostupnist-dlia-osib-z-oop/` — Доступність для навчання осіб з ООП
   - `/prozorist-ta-vidkrytist/vakansii/` — Вакансії
   - `/osvitnia-diialnist/rozklad-zaniat/` — Розклад занять
   - `/reabilitatsiine-viddilennia/korektsiino-rozvytkova-robota/` — Корекційно-розвиткова робота
   - `/reabilitatsiine-viddilennia/psykholohichna-sluzhba/` — Психологічна служба
   - `/bezpeka-ta-zakhyst/buling/` — Булінг
   - `/bezpeka-ta-zakhyst/povitriana-tryvoha/` — Увага! Повітряна тривога!
   - `/bezpeka-ta-zakhyst/bezpeka-v-interneti/` — Безпека в інтернеті
   *(Також у підвалі всіх сторінок `templates/base.html` є прямі посилання на `/pro-nas/misiya-i-viziya/`, `/pro-nas/administratsiia/` та `/pro-nas/pedahohichnyi-kolektyv/`).*

