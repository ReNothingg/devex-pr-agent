# Devex

Devex — веб-интерфейс для GitHub: открыть репозиторий, отредактировать текстовый файл, закоммитить изменения в отдельную ветку и сразу создать PR.

## Возможности

- подключение через `GITHUB_TOKEN` или GitHub App
- анонимное чтение публичных репозиториев
- просмотр дерева файлов и содержимого UTF-8 файлов
- сохранение изменений в ветку вида `devex/<suffix>`
- создание Pull Request прямо из интерфейса

## Стек

- Flask
- GitHub REST API
- Vanilla JavaScript

## Быстрый старт

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python backend/app.py
```

После запуска приложение будет доступно по адресу `http://localhost:5000`.

## Настройка `.env`

Минимальная конфигурация уже показана в `.env.example`.

- `FLASK_SECRET_KEY` — секрет Flask-сессий
- `GITHUB_TOKEN` — необязательный Personal Access Token для fallback-сценариев
- `GITHUB_APP_ID`, `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, `GITHUB_APP_SLUG` — настройки GitHub App
- `GITHUB_APP_PRIVATE_KEY_PATH` или `GITHUB_APP_PRIVATE_KEY` — приватный ключ GitHub App
- `PORT` — порт локального сервера

Пример локальной конфигурации:

```env
FLASK_SECRET_KEY=change-me
GITHUB_APP_ID=123456
GITHUB_APP_CLIENT_ID=Iv23xxxxxxxxxxxx
GITHUB_APP_CLIENT_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
GITHUB_APP_SLUG=your-app-slug
GITHUB_APP_PRIVATE_KEY_PATH=github-app.private-key.pem
PORT=5000
```

Для чтения публичных репозиториев токен не обязателен. Для коммитов и PR нужен либо `GITHUB_TOKEN` с доступом на запись, либо корректно настроенный GitHub App с правами на запись в репозиторий.

## Настройка GitHub App

Ниже — минимальная и безопасная конфигурация GitHub App для текущего сценария Devex: открыть репозиторий, изменить файл, создать отдельную ветку и открыть Pull Request.

### Что заполнить в GitHub App

При создании GitHub App укажите следующие значения:

- **GitHub App name** — любое имя приложения
- **Homepage URL** — `http://localhost:5000`
- **Callback URL** — `http://localhost:5000/auth/github/callback`
- **Setup URL** — `http://localhost:5000/auth/github/setup`

`Callback URL` должен совпадать с OAuth callback-роутом приложения, а `Setup URL` — с роутом, который обрабатывает установку GitHub App после выбора репозитория.

### Какие опции включить

Рекомендуемая конфигурация:

- **Request user authorization (OAuth) during installation** — включить
- **Expire user authorization tokens** — можно оставить выключенным на этапе локальной разработки
- **Enable Device Flow** — выключить
- **Redirect on update** — можно оставить выключенным

### Webhook

Если приложение не обрабатывает webhook-события, webhook лучше не включать.

Рекомендуемая конфигурация:

- **Webhook Active** — выключить
- **Webhook URL** — не заполнять
- **Secret** — не заполнять

### Repository permissions

Для текущего функционала Devex достаточно только следующих прав:

- **Contents** — `Read and write`
- **Pull requests** — `Read and write`
- **Metadata** — `Read-only` (обязательное разрешение GitHub)

Этого достаточно, чтобы:

- читать дерево репозитория и содержимое файлов
- создавать отдельную ветку
- сохранять изменения в файл
- открывать Pull Request из интерфейса

### Что не нужно включать

Если приложение пока не работает с Actions, workflow-файлами, issues, checks, deployments, secrets, pages или настройками репозитория, эти permissions лучше не выдавать.

Обычно **не нужны**:

- `Administration`
- `Actions`
- `Agent tasks`
- `Artifact metadata`
- `Checks`
- `Commit statuses`
- `Codespaces`
- `Deployments`
- `Environments`
- `Issues`
- `Pages`
- `Projects`
- `Secrets`
- `Variables`
- `Webhooks`
- `Workflows`

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
