# ExcelKROAutoFormat

Программа автоматически форматирует Excel-сверки инвентаризации и ищет пересорты.

- Вход: файл сверки из 1С (`.xlsx`, лист `TDSheet`).
- Выход: файл сверки с подготовленным форматированием и разметкой расхождений.
- Реализация: веб-приложение на Python (FastAPI + openpyxl) на Ubuntu Server.
- Доступ: SSH-туннель, адрес `http://localhost:8000`.

## Структура кода

| Файл | Содержание |
| --- | --- |
| `app/main.py` | Веб-слой: загрузка, отчёты, выдача файла, обучение, `/health` |
| `app/config.py` | Настройки из `.env` или окружения |
| `app/core/repair.py` | Шаг 1: ремонт файла 1С (вставка `sharedStrings.xml`) |
| `app/core/parse.py` | Шаг 2: титул, дата, блоки групп, строки итогов |
| `app/core/resort.py` | Грозди брендов и решения по расхождениям |
| `app/core/verify.py` | Второй слой: модель проверки пар пересорта |
| `app/core/learning.py` | База примеров и обучение модели |
| `app/core/embed.py` | Необязательные эмбеддинги имён (ONNX, по умолчанию выключены) |
| `app/core/format.py` | Шаги 3–9: сдвиг шапки, разметка, итоги, геометрия |
| `app/core/style.py` | Цвета, рамки, ширины, высоты, автофильтр |
| `app/core/report.py` | Таблицы отчётов для страницы |
| `app/core/pipeline.py` | Связка всех шагов |
| `deploy/` | Установка, выкладка, служба systemd, туннель |
| `tests/test_pipeline.py` | Тесты ядра на синтетическом файле |

## Документация

| Файл | Содержание |
| --- | --- |
| `docs/01-input-1c.md` | Структура входного файла из 1С |
| `docs/02-output-format.md` | Структура готового файла |
| `docs/03-transform-rules.md` | Правила преобразования |
| `docs/04-open-questions.md` | Ответы и оставшиеся вопросы |
| `docs/05-architecture.md` | Архитектура и развёртывание |
| `docs/06-resort-rules.md` | Автоматические пересорты |
| `docs/07-ml-verifier.md` | Второй слой проверки и режим обучения |

## Запуск на своём компьютере

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp deploy/.env.example .env
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Откройте `http://127.0.0.1:8000`. Проверка службы: `http://127.0.0.1:8000/health`.

Тесты: `pytest`.

## Быстрая установка на сервер

Нужно: Ubuntu Server 22.04 или новее, доступ по SSH, право `sudo`.
Замените `user@server` на свой адрес.

### Шаг 1. Команды на сервере

```bash
ssh user@server
sudo apt-get update
sudo apt-get install -y git python3 python3-venv python3-pip rsync
sudo mkdir -p /opt/excelkro
sudo chown "$USER:$USER" /opt/excelkro
git clone https://github.com/lineSence/ExcelKROAutoFormat.git /opt/excelkro
cd /opt/excelkro
chmod +x deploy/*.sh
./deploy/install.sh
curl -sS http://127.0.0.1:8000/health
exit
```

Ответ `{"status":"ok","version":"0.3.0"}` значит, что служба работает.

### Шаг 2. Команда на своём компьютере

```bash
./deploy/tunnel.sh user@server
```

Туннель держите открытым и откройте в браузере `http://127.0.0.1:8000`.

## Туннель из Windows (PowerShell)

Сервер слушает только `127.0.0.1`, поэтому страница открывается через SSH-туннель.
В Windows 10 и 11 клиент SSH уже встроен, ничего ставить не нужно.

Проверка, что клиент есть:

```powershell
ssh -V
```

Если команда не найдена: `Настройки → Приложения → Дополнительные компоненты → Добавить компонент → OpenSSH Client`.

### Открыть туннель

В PowerShell (замените `user@server` на свои данные):

```powershell
ssh -N -L 8000:127.0.0.1:8000 user@server
```

Окно остаётся открытым и ничего не печатает — так и должно быть. Пока оно открыто,
в браузере работает `http://127.0.0.1:8000`. Туннель закрывается по `Ctrl+C` или
закрытием окна.

Если SSH на нестандартном порту, добавьте `-p`:

```powershell
ssh -N -p 2222 -L 8000:127.0.0.1:8000 user@server
```

Если вход по ключу, укажите файл ключа:

```powershell
ssh -N -i $env:USERPROFILE\.ssh\id_ed25519 -L 8000:127.0.0.1:8000 user@server
```

### Туннель одной командой и сразу браузер

```powershell
Start-Process ssh -ArgumentList '-N','-L','8000:127.0.0.1:8000','user@server'
Start-Sleep -Seconds 3
Start-Process 'http://127.0.0.1:8000'
```

Закрыть такой фоновый туннель:

```powershell
Get-Process ssh | Stop-Process
```

### Ярлык на рабочем столе

Создайте файл `tunnel.ps1` (например в `C:\excelkro\tunnel.ps1`):

```powershell
$Server = 'user@server'
Write-Host 'Открываю туннель. Не закрывайте это окно.' -ForegroundColor Green
Start-Process 'http://127.0.0.1:8000'
ssh -N -L 8000:127.0.0.1:8000 $Server
```

Запуск: правый клик по файлу → «Выполнить с помощью PowerShell». Если запуск скриптов
запрещён, один раз выполните:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### Если туннель не работает

| Признак | Причина и действие |
| --- | --- |
| `bind: Address already in use` | Порт 8000 занят другим туннелем. Закройте старое окно или `Get-Process ssh \| Stop-Process` |
| `Permission denied` | Неверный логин, пароль или ключ. Проверьте `ssh user@server` без туннеля |
| Окно открыто, но страница не грузится | Служба на сервере не работает: `ssh user@server 'systemctl status excelkro'` |
| `Connection timed out` | Сервер недоступен по сети или закрыт порт SSH у провайдера |

## Установка версии со вторым слоем проверки (ветка `ml-verifier`)

В этой версии добавлены выбор режима проверки пересортов и страница обучения `/training`.
Обязательных новых зависимостей нет: модель — чистый Python, эмбеддинги по умолчанию выключены.

### Если программа уже стоит на сервере

```bash
ssh user@server
cd /opt/excelkro
git fetch origin
git checkout ml-verifier      # или: git checkout main && git pull, если ветка уже влита
git pull
venv/bin/pip install -r requirements.txt
sudo systemctl restart excelkro
curl -sS http://127.0.0.1:8000/health
```

Проверка, что второй слой на месте:

```bash
curl -sS http://127.0.0.1:8000/training | head -n 20
ls -l /opt/excelkro/data/verifier.json
```

В браузере на главной странице должен появиться выбор «Проверка пересортов», а по адресу
`http://127.0.0.1:8000/training` — страница обучения.

### Если ставите с нуля

```bash
ssh user@server
sudo apt-get update
sudo apt-get install -y git python3 python3-venv python3-pip rsync
sudo mkdir -p /opt/excelkro
sudo chown "$USER:$USER" /opt/excelkro
git clone -b ml-verifier https://github.com/lineSence/ExcelKROAutoFormat.git /opt/excelkro
cd /opt/excelkro
chmod +x deploy/*.sh
./deploy/install.sh
curl -sS http://127.0.0.1:8000/health
```

### Права на запись

База примеров и модель лежат в рабочей папке проекта, служба должна иметь право в неё писать:

```bash
cd /opt/excelkro
mkdir -p data
sudo chown -R "$(stat -c '%U' /opt/excelkro/app):$(stat -c '%G' /opt/excelkro/app)" data
ls -ld data
```

Файлы: `data/verifier.json` — обученная модель, `data/train-samples.jsonl` — примеры.
При обновлении через `git pull` они не затираются, но перед обновлением их стоит скопировать:

```bash
cp -a /opt/excelkro/data /opt/excelkro/data.backup
```

### Настройки второго слоя в `.env`

```ini
VERIFY_MODE=off              # off — только логика, model — логика + нейросеть
VERIFY_REJECT=0.05           # ниже этой вероятности пара снимается
VERIFY_WEIGHT=0.3            # вклад модели в оценку пары
VERIFIER_MODEL_PATH=data/verifier.json
TRAIN_STORE_PATH=data/train-samples.jsonl
TRAIN_EPOCHS=300
TRAIN_MAX_SAMPLES=60000
EMBED_ENABLED=false          # эмбеддинги имён, на VPS с 1 ГБ лучше не включать
```

Режим выбирается прямо на странице загрузки, `VERIFY_MODE` задаёт только значение по умолчанию.
После правки `.env` перезапустите службу: `sudo systemctl restart excelkro`.

Эмбеддинги имён (`EMBED_ENABLED=true`) требуют отдельных пакетов и файла модели:

```bash
venv/bin/pip install -r requirements-ml.txt
venv/bin/python scripts/export_embed_model.py   # создаёт models/rubert-tiny2-int8.onnx
```

На VPS с 1 ГБ памяти это необязательный шаг: без него всё работает, скорость выше.

### Откат на прежнюю версию

```bash
cd /opt/excelkro
git checkout main
sudo systemctl restart excelkro
```

### Обновление версии

С своего компьютера, из папки репозитория:

```bash
./deploy/deploy.sh user@server
```

Или на сервере:

```bash
cd /opt/excelkro
git pull
venv/bin/pip install -r requirements.txt
sudo systemctl restart excelkro
```

### Полезные команды службы

```bash
sudo systemctl status excelkro     # состояние
sudo systemctl restart excelkro    # перезапуск
sudo systemctl stop excelkro       # остановка
journalctl -u excelkro -n 100      # последние записи журнала
```

Настройки лежат в `/opt/excelkro/.env` (образец — `deploy/.env.example`). После правки файла перезапустите службу.

### Если не работает

| Признак | Причина и действие |
| --- | --- |
| `curl` даёт ошибку соединения | Служба не запустилась. Смотрите `journalctl -u excelkro -n 100` |
| Страница не открывается в браузере | Туннель закрыт. Запустите туннель заново |
| Ошибка при `pip install` | Нет доступа в сеть с сервера. Нужен выход к PyPI или своё зеркало |
| Порт 8000 занят | Измените `APP_PORT` в `.env` и порт в `deploy/app.service` |
| Вариант «логика + нейросеть» недоступен | Нет файла модели. Обучите её на странице `/training` или проверьте `VERIFIER_MODEL_PATH` |
| Ошибка записи при обучении | Нет прав на папку `data`. Смотрите раздел «Права на запись» |

## Выкладка на сервер

```bash
./deploy/deploy.sh user@server   # копирование и перезапуск службы
./deploy/tunnel.sh user@server   # туннель на локальный порт 8000
```

Первая установка на сервере: `./deploy/install.sh` (создаёт окружение и службу `excelkro`).

## Граница автоматизации

Программа делает автоматически:

- ремонт файла из 1С;
- разбор блоков групп;
- сдвиг шапки и геометрию;
- поиск пересортов и разметку строк (рамка, жёлтый, оранжевый, метка `не+`);
- проверку пар вторым слоем, если выбран режим с нейросетью;
- формулы итогов групп и общего итога;
- автофильтр и имя выходного файла.

Человек вписывает в Excel вручную: неучтёнку (`B1`), контрольное число (`C1`), дату (`B3`), признак (`C5`), итог с неучтёнкой (`I5`), комментарии столбца `L`, блок сотрудников. Также он проверяет решения программы по спорным строкам.

## Статус

Код версии 0.3.0. Логика пересортов сверена с двумя ручными сверками, второй слой проверки и режим обучения доступны в интерфейсе.
