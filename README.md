# Devex

Devex — приложение для GitHub которое может открыть репозиторий, отредактировать текстовый файл, закоммитить изменения в отдельную ветку и сразу создать PR.

## Старт

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python backend/app.py
```

После запуска приложение будет доступно по адресу `http://localhost:5000`.

## Локальная разработка

Для локальной разработки вам потребуется установить зависимости и запустить приложение.

1.  **Установка зависимостей:**
    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    ```

2.  **Настройка переменных окружения:**
    Скопируйте пример файла `.env` и настройте его:
    ```bash
    cp .env.example .env
    ```
    Затем отредактируйте файл `.env` согласно вашим настройкам (см. раздел "Настройка `.env`" ниже).

3.  **Запуск приложения:**
    ```bash
    python backend/app.py
    ```
    После запуска приложение будет доступно по адресу `http://localhost:5000`.

## Настройка `.env`

Мин. конфигурация уже показана в `.env.example`.

- `FLASK_SECRET_KEY` — секрет Flask-сессий
- `GITHUB_TOKEN` — необязательный Personal Access Token для fallback-сценариев
- `GITHUB_APP_ID`, `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, `GITHUB_APP_SLUG` — настройки GitHub App
- `GITHUB_APP_PRIVATE_KEY_PATH` или `GITHUB_APP_PRIVATE_KEY` — приватный ключ GitHub App
- `PORT` — порт локального сервера

Для чтения публичных репозиториев токен не обязателен. Для коммитов и PR нужен либо `GITHUB_TOKEN` с доступом на запись, либо корректно настроенный GitHub App с правами на запись в репозиторий.

## Настройка GitHub App

### Что заполнить в GitHub App

При создании GitHub App укажите следующие значения:

- **GitHub App name** — любое имя приложения
- **Homepage URL** — `http://localhost:5000` (или другой адрес, по которому будет доступно приложение)
- **Callback URL** — `http://localhost:5000/auth/github/callback`
- **Setup URL** — `http://localhost:5000/auth/github/setup`

`Callback URL` должен совпадать с OAuth callback-роутом приложения, а `Setup URL` — с роутом, который обрабатывает установку GitHub App после выбора репозитория.

### Какие опции включить

Metadata — Read-only

Contents — Read and write

Pull requests — Read and write

Issues — Read and write

Commit statuses — Read and write

Checks — Read and write

Actions — Read-only

Workflows — Read and write

Pages — Read-only

Deployments — Read-only

Code scanning alerts — Read-only

Dependabot alerts — Read-only

Secret scanning alerts — Read-only

Discussions — Read-only или выключить.

Packages — Read-only

Webhooks — выключить.

Administration — не давать.

Secrets — не давать.

Variables — выключить или Read-only.

Environments — выключить или Read-only.Organization permissions: ничего не давай.

Account permissions:
Email addresses — Read-only, только если нужен email пользователя.
Profile — Read-only, если нужен username/avatar/name.

Subscribe to events:

Installation
Installation repositories
Pull request
Push
Issues — если используешь issues
Issue comment — если хочешь команды типа /devex fix в issue/PR comments
Check run / Check suite — если работаешь с checks
Workflow run — если хочешь ловить результат CI
Repository — опционально

Чем меньше прав выдано приложению, тем безопаснее установка.

### Где можно устанавливать приложение

Для локальной разработки и первых тестов лучше выбрать:

- **Only on this account**

Так GitHub App сможет устанавливаться только в пределах нужного аккаунта, без лишнего доступа к другим пользователям или организациям.

### Важный момент после изменения permissions

Если permissions уже были изменены, но приложение всё ещё не может пушить изменения, проверьте следующее:

1. GitHub App переустановлена после изменения permissions
2. App установлена именно на нужный репозиторий
3. У выбранной установки есть доступ к нужному репозиторию
4. Текущая сессия в приложении использует правильную installation

Если приложение умеет читать репозиторий, но не может создать коммит или PR, чаще всего причина именно в том, что GitHub App установлена без `Contents: Read and write` и `Pull requests: Read and write`.

## Как пользоваться

1. Подключите GitHub или вставьте токен.
2. Укажите ссылку на репозиторий и базовую ветку.
3. Откройте нужный файл и внесите изменения.
4. Задайте суффикс ветки и сообщение коммита.
5. Создайте коммит, затем откройте PR.
