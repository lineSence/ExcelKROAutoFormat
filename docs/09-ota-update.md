# Обновление по кнопке (OTA)

Страница `Обновление` (`/update`) ставит на сервер последнюю версию из Git:
забирает код, доставляет зависимости и перезапускает службу.

## Как это устроено

Служба `excelkro` работает под ограниченным пользователем и не может писать
в `/opt/excelkro` (`ProtectSystem=strict`). Поэтому обновляет её не она сама, а
разовая служба `excelkro-update@<режим>`:

1. веб-слой вызывает `sudo systemctl start --no-block excelkro-update@apply.service`;
2. служба запускает `bash deploy/ota-update.sh` под root в своём окружении;
3. скрипт пишет ход работы в `/var/lib/excelkro/update/update-state.json` и `update.log`;
4. страница читает эти файлы и обновляется сама каждые 5 секунд, пока идёт работа.

Отдельная служба нужна и по второй причине: при перезапуске веб-слой умирает.
Если запустить обновление дочерним процессом, оно оборвётся на полпути.

Скрипт запускается именно через `bash`, а не напрямую. Причина: обновление
делает `git reset --hard`, а git возвращает файлам права из репозитория и
снимает выставленный вручную флаг `+x`. Раньше из-за этого после каждого
обновления страница снова требовала `install-ota.sh`.

## Режимы

- `check` — кнопка «Проверить обновления»: только `git fetch` и сравнение сборок.
  Файлы не меняются, служба не перезапускается.
- `apply` — кнопка «Установить последнюю версию»: `git reset --hard` на `FETCH_HEAD`,
  `pip install -r requirements.txt`, при необходимости обновление `app.service`
  и `excelkro-update@.service`, `systemctl restart excelkro` и проверка `/health`
  до 20 секунд.

Если новых коммитов нет, `apply` ничего не делает и пишет об этом в состояние.

Описание службы обновления переставляется автоматически только при стандартных
путях (`/opt/excelkro`, `/var/lib/excelkro/update`). На нестандартной установке
после изменения unit-файла нужен `install-ota.sh`.

## Данные при обновлении

Остаются на месте: справочники, реестр расхождений, примеры обучения, модель —
они живут вне папки кода (`/var/lib/excelkro`).

Теряются: незаконченные сверки, ссылки на скачивание и подтверждённые фото —
они хранятся в памяти процесса. Поэтому кнопка спрашивает подтверждение.

## Установка на сервере

На новом сервере всё делает `deploy/install.sh`.

Если сервер разворачивался или обновлялся вручную и частей OTA на нём нет
(страница пишет «Служба обновления не установлена»), достаточно одной команды:

```bash
sudo bash /opt/excelkro/deploy/install-ota.sh
```

Скрипт трогает только части обновления: ставит unit, кладёт правило sudo,
создаёт папку состояния и перечитывает `systemd`. Саму программу и данные он
не меняет. Правило sudo проверяется `visudo -c` и при ошибке удаляется, чтобы
не сломать sudo на сервере.

Те же шаги вручную:

```bash
sudo cp /opt/excelkro/deploy/excelkro-update@.service /etc/systemd/system/
sudo install -m 440 /opt/excelkro/deploy/sudoers-excelkro /etc/sudoers.d/excelkro
sudo visudo -cf /etc/sudoers.d/excelkro
sudo mkdir -p /var/lib/excelkro/update
sudo chown -R excelkro:excelkro /var/lib/excelkro/update
sudo systemctl daemon-reload
```

Право sudo дано только на запуск двух именованных служб: других команд под
`root` веб-слой выполнить не может. Разрешены оба обычных пути к `systemctl`
(`/usr/bin` и `/bin`): он различается по дистрибутивам.

Если служба работает от root (частый случай ручного разворота), правило sudo
вообще не нужно: `install-ota.sh` его удаляет, а программа запускает
`systemctl` напрямую. Проверка готовности это учитывает.

## Если папка программы не копия Git

При развороте через `deploy/deploy.sh` код приезжает через rsync, и `.git` нет.
Тогда кнопка отключена и на странице видно предупреждение. Переход одноразовый:

```bash
sudo systemctl stop excelkro
sudo mv /opt/excelkro /opt/excelkro-old
sudo git clone https://github.com/lineSence/ExcelKROAutoFormat.git /opt/excelkro
sudo cp -r /opt/excelkro-old/venv /opt/excelkro/venv
sudo bash /opt/excelkro/deploy/install.sh
```

## Проверка и разбор ошибок

- готовность частей OTA видна на самой странице «Обновление» и в `parts` ответа
  `curl -sS http://127.0.0.1:8000/update/state`;
- журнал скрипта: `/var/lib/excelkro/update/update.log`;
- журнал службы обновления: `journalctl -u excelkro-update@apply`;
- журнал программы: `journalctl -u excelkro -n 100`;
- права служебного пользователя: `sudo -u excelkro sudo -n -l`.

Сообщение «Служба обновления не установлена» перечисляет, чего именно
не хватает, и даёт команду доустановки. Если всё установлено, а запуск всё
равно не идёт, в сообщении будет последняя строка ответа `sudo` — обычно
это несовпадение пути к `systemctl` или ошибка в правиле sudo.

Если на старом сервере в `/etc/systemd/system/excelkro-update@.service` осталась
строка `ExecStart=/opt/excelkro/deploy/ota-update.sh %i` (без `bash`), обновите
описание одной командой `sudo bash /opt/excelkro/deploy/install-ota.sh`.
