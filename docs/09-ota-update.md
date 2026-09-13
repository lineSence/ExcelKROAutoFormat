# Обновление по кнопке (OTA)

Страница `Обновление` (`/update`) ставит на сервер последнюю версию из Git:
забирает код, доставляет зависимости и перезапускает службу.

## Как это устроено

Служба `excelkro` работает под ограниченным пользователем и не может писать
в `/opt/excelkro` (`ProtectSystem=strict`). Поэтому обновляет её не она сама, а
разовая служба `excelkro-update@<режим>`:

1. веб-слой вызывает `sudo systemctl start --no-block excelkro-update@apply.service`;
2. служба запускает `deploy/ota-update.sh` под root в своём окружении;
3. скрипт пишет ход работы в `/var/lib/excelkro/update/update-state.json` и `update.log`;
4. страница читает эти файлы и обновляется сама каждые 5 секунд, пока идёт работа.

Отдельная служба нужна и по второй причине: при перезапуске веб-слой умирает.
Если запустить обновление дочерним процессом, оно оборвётся на полпути.

## Режимы

- `check` — кнопка «Проверить обновления»: только `git fetch` и сравнение сборок.
  Файлы не меняются, служба не перезапускается.
- `apply` — кнопка «Установить последнюю версию»: `git reset --hard` на `FETCH_HEAD`,
  `pip install -r requirements.txt`, при необходимости обновление `app.service`,
  `systemctl restart excelkro` и проверка `/health` до 20 секунд.

Если новых коммитов нет, `apply` ничего не делает и пишет об этом в состояние.

## Данные при обновлении

Остаются на месте: справочники, реестр расхождений, примеры обучения, модель,
`.env` — они живут вне папки кода (`/var/lib/excelkro`).

Теряются: незаконченные сверки и ссылки на скачивание — они хранятся в памяти
процесса. Поэтому кнопка спрашивает подтверждение.

## Установка на сервере

`deploy/install.sh` делает всё сам. Вручную шаги такие:

```bash
sudo cp /opt/excelkro/deploy/excelkro-update@.service /etc/systemd/system/
sudo chmod 755 /opt/excelkro/deploy/ota-update.sh
sudo install -m 440 /opt/excelkro/deploy/sudoers-excelkro /etc/sudoers.d/excelkro
sudo visudo -cf /etc/sudoers.d/excelkro
sudo mkdir -p /var/lib/excelkro/update
sudo chown -R excelkro:excelkro /var/lib/excelkro/update
sudo systemctl daemon-reload
```

Право sudo дано только на запуск двух именованных служб: других команд под
`root` веб-слой выполнить не может.

## Если папка программы не копия Git

При развороте через `deploy/deploy.sh` код приезжает через rsync, и `.git` нет.
Тогда кнопка отключена и на странице видно предупреждение. Переход одноразовый:

```bash
sudo systemctl stop excelkro
sudo mv /opt/excelkro /opt/excelkro-old
sudo git clone https://github.com/lineSence/ExcelKROAutoFormat.git /opt/excelkro
sudo cp /opt/excelkro-old/.env /opt/excelkro/.env
sudo cp -r /opt/excelkro-old/venv /opt/excelkro/venv
sudo bash /opt/excelkro/deploy/install.sh
```

## Проверка и разбор ошибок

- состояние в JSON: `curl -sS http://127.0.0.1:8000/update/state`;
- журнал скрипта: `/var/lib/excelkro/update/update.log`;
- журнал службы обновления: `journalctl -u excelkro-update@apply`;
- журнал программы: `journalctl -u excelkro -n 100`.

Сообщение «Служба обновления не запускается» означает, что нет unit-а или
правила sudo — повторите шаги из раздела «Установка на сервере».
