# ПЛАН РАЗРАБОТКИ — Instagram Analytics Telegram Bot

## Общая архитектура

Пользователь (шеф) → Telegram Bot → Сбор данных из Instagram API →
Анализ данных → ИИ-генерация инсайтов → Формирование отчёта + графики →
Ответ пользователю в Telegram

---

## ШАГ 1: Инициализация проекта

### Структура:

```
d:\Code Projects\ReportInsta_tgBOT\
├── .env.example
├── .gitignore
├── requirements.txt
├── config.py
├── main.py
├── database/
│   ├── __init__.py
│   ├── models.py
│   ├── engine.py
│   └── crud.py
├── instagram/
│   ├── __init__.py
│   └── client.py
├── analytics/
│   ├── __init__.py
│   ├── calculations.py
│   └── ai_analyzer.py
├── reports/
│   ├── __init__.py
│   ├── generator.py
│   └── charts.py
├── bot/
│   ├── __init__.py
│   ├── handlers.py
│   └── keyboards.py
└── utils/
    ├── __init__.py
    └── formatters.py
```

### Файлы:

- `.gitignore` — Python, .env, __pycache__, .venv
- `.env.example` — BOT_TOKEN, OPENAI_API_KEY, DATABASE_URL, ADMIN_ID
- `requirements.txt` — aiogram>=3.0, sqlalchemy>=2.0, asyncpg, aiohttp, openai, matplotlib, python-dotenv

---

## ШАГ 2: Конфигурация (`config.py`)

- Класс Settings из .env через python-dotenv
- Поля: BOT_TOKEN, OPENAI_API_KEY, DATABASE_URL, ADMIN_TELEGRAM_ID
- ACCOUNTS: список словарей {name, instagram_user_id, page_id, access_token}
- OPENAI_MODEL = "gpt-4-turbo"
- CHART_STYLE = "seaborn-v0_8-whitegrid"

---

## ШАГ 3: База данных

### Модели (`database/models.py`):

1. **Account**
   - id: Integer PK
   - instagram_user_id: String (ID из Instagram)
   - username: String (@handle)
   - name: String (отображаемое имя)
   - access_token: String (зашифрованный)
   - facebook_page_id: String
   - is_active: Boolean (default True)
   - created_at: DateTime
   - updated_at: DateTime

2. **DailyStats** (снимок за день)
   - id: Integer PK
   - account_id: Integer FK→Account
   - date: Date (уникальная пара с account_id)
   - followers: Integer
   - following: Integer
   - media_count: Integer
   - reach: Integer
   - impressions: Integer
   - profile_views: Integer
   - engagement: Integer (суммарные взаимодействия)
   - UNIQUE(account_id, date)

3. **Post**
   - id: Integer PK
   - account_id: Integer FK→Account
   - instagram_media_id: String (уникальный)
   - media_type: String (IMAGE/VIDEO/CAROUSEL/REELS)
   - caption: Text
   - permalink: String
   - timestamp: DateTime (дата публикации)
   - likes: Integer
   - comments: Integer
   - saved: Integer
   - shares: Integer
   - reach: Integer
   - impressions: Integer
   - views: Integer (для Reels/Video)
   - engagement: Integer (likes+comments+saved+shares)
   - UNIQUE(instagram_media_id)

### Подключение (`database/engine.py`):

- AsyncEngine с asyncpg
- async_sessionmaker
- init_db() — создание таблиц через metadata.create_all

### CRUD (`database/crud.py`):

- save_or_update_account()
- get_all_accounts() / get_account_by_username()
- save_daily_stats() — upsert по (account_id, date)
- get_daily_stats(account_id, date_from, date_to) — отсортированные по дате
- get_latest_stats(account_id) — последний снимок
- get_stats_for_date(account_id, date) — снимок на конкретную дату
- save_posts() — bulk upsert
- get_posts(account_id, date_from, date_to) — с сортировкой
- get_post_by_media_id()
- get_top_posts(account_id, date_from, date_to, metric, limit)

---

## ШАГ 4: Instagram API клиент (`instagram/client.py`)

### Класс InstagramClient:

- __init__(access_token, page_id)
- Все методы async через aiohttp

### Методы и логика:

1. **get_user_info()**
   - GET /{ig-user-id}?fields=id,username,name,biography,followers_count,follows_count,media_count,profile_picture_url
   - Возвращает dict

2. **get_account_insights(since, until)**
   - GET /{ig-user-id}/insights?metric=reach,impressions,profile_views,engagement&period=day&since={unix}&until={unix}
   - Период day — получаем данные за каждый день отдельно
   - ПРОБЛЕМА: API возвращает данные по period=day за max 90 дней
   - РЕШЕНИЕ: запрашиваем за нужный период, парсим массив data[]
   - Каждый элемент data[] содержит {name, period, values: [{value, end_time}]}
   - Преобразуем в словарь {date: {metric: value}}

3. **get_media_list(limit=50)**
   - GET /{ig-user-id}/media?fields=id,caption,media_type,media_url,permalink,timestamp,like_count,comments_count&limit={limit}
   - Пагинация через paging.next
   - Возвращает list[dict]

4. **get_media_insights(media_id, media_type)**
   - Для IMAGE: metric=engagement,impressions,reach,saved
   - Для VIDEO: metric=engagement,impressions,reach,saved,video_views
   - Для CAROUSEL: metric=carousel_album_engagement,carousel_album_impressions,carousel_album_reach,carousel_album_saved,carousel_album_video_views
   - Для REELS: metric=comments,likes,reach,saved,shares,total_interactions,views
   - GET /{media-id}/insights?metric={metrics}
   - ПРОБЛЕМА: разные метрики для разных типов
   - РЕШЕНИЕ: маппинг метрик по media_type в словаре MEDIA_METRICS_MAP

5. **get_followers_growth(since, until)**
   - Из get_account_insights берём metric=follower_count с period=day
   - Если нет — считаем из разницы daily_stats

6. **collect_full_snapshot()**
   - Вызывает get_user_info() + get_account_insights() за сегодня
   - Возвращает готовый объект для сохранения в DailyStats

7. **collect_posts_with_insights(date_from, date_to)**
   - Получает список медиа → для каждого получает insights
   - Фильтрует по дате публикации
   - Возвращает list[dict] с полными данными

### Обработка ошибок:

- Rate limit (400 ошибка с кодом 4) → retry после ожидания
- Token expired (190) → логирование + уведомление админу
- Network errors → retry 3 раза с exponential backoff
- Логирование всех API-вызовов

---

## ШАГ 5: Аналитика — расчёты (`analytics/calculations.py`)

### ВАЖНО: Логика расчётов должна быть точной

1. **calculate_growth(current, previous)**
   - Абсолютный прирост: current - previous
   - Процентный прирост: ((current - previous) / previous) * 100
   - Граничный случай: previous == 0 → return None (нельзя делить на 0)

2. **calculate_period_summary(stats_list)**
   - Вход: список DailyStats за период
   - Выход:
     - followers_start, followers_end, followers_growth, followers_growth_pct
     - reach_total, impressions_total, engagement_total, profile_views_total
     - reach_avg_daily, impressions_avg_daily, engagement_avg_daily
   - Логика: берём первый и последний день для подписчиков, суммируем остальное

3. **calculate_content_summary(posts_list)**
   - Вход: список Post за период
   - Выход:
     - total_posts, total_reels, total_carousels, total_images
     - total_likes, total_comments, total_saves, total_shares
     - avg_likes, avg_comments, avg_reach
     - engagement_rate = (total_engagement / total_reach) * 100
   - Граничный случай: нет постов → все поля 0

4. **score_post(post) → float**
   - Взвешенный скор для определения "лучшего поста":
     score = (likes * 1) + (comments * 2) + (saved * 3) + (shares * 4)
   - Комментарии, сохранения и репосты весят больше — они показывают deeper engagement
   - Нормализация: score / reach * 100 (вовлечённость на охват)

5. **get_best_post(posts, content_type=None)**
   - Сортирует по score_post()
   - Если content_type='REELS' — фильтрует только Reels
   - Возвращает top-1 пост с полными данными

6. **get_worst_post(posts)**
   - Тот же скор, но минимальный
   - Помогает понять, что не работает

7. **detect_trend(stats_list, metric, window=7)**
   - Скользящее среднее за window дней
   - Сравнение последнего значения с MA
   - Возвращает: 'growing' / 'declining' / 'stable'

8. **compare_accounts(accounts_data)**
   - Вход: список {account_name, period_summary, content_summary}
   - Рейтинг по категориям:
     - Лучший по росту: followers_growth_pct
     - Лучший по вовлечённости: engagement_rate
     - Лучший по охвату: reach_total
   - Возвращает {category: {account, value}}

---

## ШАГ 6: ИИ-анализатор (`analytics/ai_analyzer.py`)

### Класс AIAnalyzer:

- __init__(api_key, model="gpt-4-turbo")

### Методы и ПРОМПТЫ (ключ к качеству!):

1. **analyze_account(stats_summary, content_summary, best_post, trend) → str**

   ПРОМПТ:
   ```
   Ты — senior SMM-аналитик. Проанализируй статистику Instagram-аккаунта.

   ДАННЫЕ:
   Период: {period}
   Подписчики: {followers} (прирост: {growth}, {growth_pct}%)
   Охват: {reach} | Показы: {impressions} | Посещения профиля: {profile_views}
   Взаимодействия: {engagement} | Вовлечённость: {engagement_rate}%
   Контент: {total_posts} публикаций (Reels: {reels}, Image: {images}, Carousel: {carousels})
   Средние показатели: лайки {avg_likes}, комменты {avg_comments}, охват {avg_reach}
   Лучший пост: {best_post_info}
   Тренд подписчиков: {trend}

   ЗАДАЧА:
   1. Дай 3-5 конкретных выводов о состоянии аккаунта
   2. Укажи сильные и слабые стороны
   3. Оцени качество контента по цифрам
   4. Дай 2-3 конкретные рекомендации

   ФОРМАТ: Без воды, только конкретика с цифрами. Эмодзи допускаются.
   Не повторяй данные, которые уже есть в отчёте — давай ДОПОЛНИТЕЛЬНУЮ ценность.
   ```

2. **analyze_comparison(accounts_summary) → str**

   ПРОМПТ:
   ```
   Ты — senior SMM-аналитик. Сравни несколько Instagram-аккаунтов.

   ДАННЫЕ АККАУНТОВ:
   {formatted_accounts_data}

   ЗАДАЧА:
   1. Сравни ключевые показатели
   2. Определи лидера в каждой категории и объясни почему
   3. Выяви общие тренды между аккаунтами
   4. Дай рекомендации для каждого аккаунта
   5. Предложи кросс-стратегию (что один аккаунт может перенять у другого)

   ФОРМАТ: Структурированно, с чёткими выводами.
   ```

3. **generate_recommendations(stats_summary, content_summary, ai_analysis) → str**

   ПРОМПТ:
   ```
   На основе анализа ({ai_analysis}) и данных ({data}), сгенерируй 3-5 ПРАКТИЧЕСКИХ рекомендаций:
   - Конкретные действия (не общие слова)
   - Привязка к цифрам
   - Ожидаемый эффект
   - Приоритет (высокий/средний/низкий)
   ```

### Обработка:

- timeout: 30 секунд
- max_tokens: 1000
- temperature: 0.3 (низкий для точности)
- При ошибке OpenAI → возвращаем заглушку "Анализ временно недоступен"
- Логирование стоимости каждого запроса

---

## ШАГ 7: Графики (`reports/charts.py`)

### Используем matplotlib с русскими шрифтами

1. **create_followers_chart(dates, followers_values) → bytes**
   - Линейный график с точками
   - Заливка области под линией
   - Подписи значений на ключевых точках
   - Заголовок: "Динамика подписчиков"

2. **create_metrics_chart(dates, reach_values, impressions_values) → bytes**
   - Две линии: охват и показы
   - Легенда
   - Заголовок: "Охват и показы"

3. **create_engagement_chart(dates, engagement_values) → bytes**
   - Столбчатая диаграмма
   - Средняя линия
   - Заголовок: "Взаимодействия по дням"

4. **create_content_comparison_chart(posts_data) → bytes**
   - Scatter plot: охват vs вовлечённость
   - Цветовая кодировка по типу контента
   - Заголовок: "Эффективность контента"

5. **create_accounts_comparison_chart(accounts_data) → bytes**
   - Горизонтальные столбцы: подписчики, охват, вовлечённость для каждого аккаунта
   - Заголовок: "Сравнение аккаунтов"

### Общие настройки:

- Размер: 10x6 дюймов
- Цветовая палитра: #2196F3, #4CAF50, #FF9800, #F44336
- Формат: PNG в bytes (для отправки через Telegram)
- Русские подписи (matplotlib rcParams font)

---

## ШАГ 8: Генератор отчётов (`reports/generator.py`)

### ТИПЫ ОТЧЁТОВ:

1. **generate_report(account, period="week") → dict**

   Возвращает {text: str, chart_followers: bytes, chart_metrics: bytes, chart_content: bytes}

   ЛОГИКА:
   1. Определяем date_from и date_to по period
   2. Запрашиваем данные из БД
   3. Если данных нет → запрашиваем свежие из API → сохраняем → используем
   4. Считаем period_summary
   5. Считаем content_summary
   6. Определяем лучший пост
   7. Определяем тренд
   8. Генерируем ИИ-анализ
   9. Формируем текстовый отчёт
   10. Генерируем графики
   11. Возвращаем всё вместе

   ФОРМАТ ОТЧЁТА:
   ```
   📱 @{username} — {name}
   📅 Период: {date_from} — {date_to}

   👥 Подписчики
   Текущие: {current:,}
   Прирост: {growth} ({growth_pct}%)
   Тренд: {trend_emoji} {trend_text}

   📈 Активность
   Охват: {reach:,}
   Показы: {impressions:,}
   Взаимодействия: {engagement:,}
   Посещения профиля: {profile_views:,}
   Вовлечённость: {engagement_rate}%

   📹 Контент
   Опубликовано: {total_posts}
   Reels: {reels} | Фото: {images} | Карусели: {carousels}
   Средние лайки: {avg_likes}
   Средний охват: {avg_reach}

   🏆 Лучший пост
   {best_post_description}

   🤖 AI-анализ
   {ai_analysis}

   💡 Рекомендации
   {ai_recommendations}
   ```

2. **generate_comparison_report(accounts) → dict**
   - Собирает данные по всем аккаунтам
   - Считает рейтинги
   - Генерирует ИИ-сравнение
   - Формирует сводную таблицу

3. **generate_dynamics_report(account, metric="followers", days=30) → dict**
   - Динамика конкретной метрики
   - Тренд + прогноз
   - График

### ВАЖНО: Логика свежести данных:

- Если latest_stats.date == today → используем кэш
- Если latest_stats.date < today → собираем свежие данные
- Если данных совсем нет → collect_full_snapshot + collect_posts_with_insights

---

## ШАГ 9: Telegram бот

### Клавиатуры (`bot/keyboards.py`):

- main_menu_kb() — ReplyKeyboard: [📊 Отчёты] [📈 Динамика] [📱 Аккаунты] [👥 Сравнение]
- accounts_kb() — Inline: кнопки для каждого аккаунта
- period_kb() — Inline: [День] [Неделя] [Месяц] [Произвольный]
- back_kb() — Inline: [◀️ Назад]

### Обработчики (`bot/handlers.py`):

1. **cmd_start** → приветствие + главное меню
2. **📊 Отчёты** → "Выберите аккаунт" → accounts_kb()
3. **callback:account_{id}** → "Выберите период" → period_kb()
4. **callback:period_{type}** →
   - Показываем "⏳ Собираю данные..."
   - Вызываем generate_report()
   - Отправляем текст отчёта
   - Отправляем графики как фото (InputMediaPhoto)
5. **📈 Динамика** → выбор аккаунта → выбор метрики → generate_dynamics_report()
6. **📱 Аккаунты** → список с краткой статистикой (последний снимок)
7. **👥 Сравнение** → generate_comparison_report()

### Middleware:

- CheckAdminMiddleware — проверяет message.from_user.id == ADMIN_ID
- Все остальные получают "Доступ запрещён"

### Обработка ошибок:

- try/except вокруг каждого хендлера
- При ошибке → "Произошла ошибка. Попробуйте позже." + лог
- Timeout на генерацию отчёта: 60 секунд

---

## ШАГ 10: Точка входа (`main.py`)

1. Загрузка config
2. init_db()
3. Создание aiogram Bot + Dispatcher
4. Регистрация middleware
5. Регистрация хендлеров
6. Проверка подключения к Instagram API (пробный запрос)
7. Запуск polling

---

## ШАГ 11: Утилиты (`utils/formatters.py`)

- format_number(n) → "12 540" (с пробелами)
- format_pct(n) → "+5.3%" / "-2.1%"
- format_date(d) → "1 сентября"
- format_period(from, to) → "1–7 сентября"
- format_growth(n) → "📈 +640" / "📉 -120"
- truncate_text(text, max_len) → обрезка для Telegram (4096 символов)

---

## ШАГ 12: Тестирование

1. Ручной запуск бота
2. /start → проверка меню
3. /accounts → проверка списка
4. /analyze → проверка полного цикла
5. Проверка корректности расчётов вручную
6. Проверка генерации графиков
7. Проверка ИИ-анализа на адекватность
8. Обработка ошибок (отключить токен → проверить сообщение)

---

## ПОРЯДОК РЕАЛИЗАЦИИ

1. config.py + .env.example + requirements.txt + .gitignore
2. database/ (models → engine → crud)
3. instagram/client.py
4. analytics/calculations.py
5. reports/charts.py
6. analytics/ai_analyzer.py
7. reports/generator.py
8. utils/formatters.py
9. bot/keyboards.py
10. bot/handlers.py
11. main.py
12. Тестирование и отладка

---

## ОТ ТЕБЯ НУЖНО ПЕРЕД НАЧАЛОМ

1. Telegram Bot Token (@BotFather)
2. OpenAI API Key (platform.openai.com)
3. Instagram API данные (3 аккаунта):
   - Instagram Business Account ID
   - Facebook Page ID
   - Long-lived Access Token
4. Твой Telegram user_id (@userinfobot)