# Unified Deploy Blueprint For Ubuntu

Этот файл является каноническим blueprint для production-like деплоя проекта на
Ubuntu-сервере.

Цель файла:

- зафиксировать один целевой operational сценарий;
- не пересобирать архитектуру заново в следующих сессиях;
- использовать этот документ как source of truth при дальнейшем внедрении.

Этот blueprint основан на текущем состоянии репозитория и на существующем
документе [android-native-youtube-runtime-plan.md](./android-native-youtube-runtime-plan.md).

## 1. Главная цель

На выходе проект должен запускаться максимально просто:

```bash
./ops/up.sh
```

И так же просто останавливаться:

```bash
./ops/down.sh
```

Дополнительно:

```bash
./ops/status.sh
./ops/logs.sh
```

Ключевой принцип:

- для пользователя это один стек и один вход;
- внутри допускается гибрид `docker compose + systemd`, если это даёт
  устойчивость и простоту эксплуатации;
- Android runtime не заталкивается в Docker любой ценой.

## 2. Почему не делать "всё в одном compose"

Для Ubuntu-сервера хочется один compose, но для Android это неверная цель.

Причины:

- Android emulator требует аппаратной виртуализации;
- поведение emulator внутри Docker значительно менее предсказуемо;
- Appium и AVD логически лучше живут как host-managed runtime;
- проект уже сам ожидает локальные `adb`, `emulator`, `sdkmanager`,
  `avdmanager`, `appium`, `java`;
- существующий Android runtime в коде рассчитан на host tools, а не на
  container-only среду.

Вывод:

- единый запуск нужен;
- единый механизм внутри не обязателен;
- целевой вариант для Ubuntu: `compose` для web/backend части и `systemd` для
  Android host-runtime.

## 3. Целевой UX

После однократного bootstrap на сервере оператор должен работать так:

```bash
cd /opt/youtube_automation
./ops/up.sh
```

Скрипт должен:

1. проверить обязательные зависимости;
2. поднять Android host-runtime;
3. поднять container stack;
4. дождаться healthchecks;
5. вывести итоговый статус и публичный URL.

Ожидаемый результат:

- UI открывается по одному HTTPS URL;
- backend API доступен через тот же домен по `/api`;
- desktop Playwright worker работает;
- Android worker работает;
- Postgres и Redis не торчат наружу;
- остановка тоже идёт одной командой.

Если код загружается на Ubuntu-сервер не через `git pull`, а архивом с macOS,
архив должен собираться без AppleDouble-файлов `._*`.

Минимальное правило:

- использовать `COPYFILE_DISABLE=1`;
- исключать `._*` и `.DS_Store`;
- не отправлять `artifacts/` в deploy archive.

## 4. Целевая топология

### 4.1. Public edge

Один публичный ingress:

- `gateway` container на `80/443`;
- он раздаёт собранный frontend как static SPA;
- он проксирует `/api` на backend API;
- он же корректно проксирует SSE/status stream без буферизации.

### 4.2. Container stack

Через `docker compose` поднимаются:

- `gateway`
- `api`
- `migrator`
- `scheduler`
- `worker_browser`
- `worker_analysis`
- `postgres`
- `redis`

Опционально отдельным profile:

- `grafana`
- `loki`
- `prometheus`
- `promtail`

### 4.3. Host-managed Android stack

Через `systemd` поднимаются:

- `yta-appium.service`
- `yta-android-worker.service`
- `yta-android.target`

Android emulator не должен быть обязательным отдельным always-on daemon.
Правильнее:

- Appium живёт как сервис;
- Android worker живёт как сервис;
- emulator lifecycle управляется worker-ом через текущий runtime-код.

## 5. Почему это считается "одним целым"

Потому что operator interface остаётся единым:

- один каталог;
- один набор env-файлов;
- один набор `ops/*.sh`;
- один `up/down/status/logs`;
- один публичный URL;
- один согласованный deploy blueprint.

Это важнее, чем формально уместить всё в один compose-файл.

## 6. Базовые требования к Ubuntu-серверу

Ниже не "абсолютный минимум", а рабочий baseline.

### 6.1. Хост

Рекомендуемый baseline:

- Ubuntu LTS;
- x86_64;
- systemd;
- Docker Engine + Compose plugin;
- доступный `KVM`;
- публичный домен для HTTPS.

Инженерная оценка по ресурсам:

- минимум: `8 vCPU`, `16 GB RAM`, `120 GB SSD`;
- комфортнее: `12+ vCPU`, `24-32 GB RAM`, `150+ GB SSD`.

Если на сервере нет `KVM` или nested virtualization недоступна:

- Android runtime считать неподдерживаемым;
- desktop/runtime через Playwright всё ещё можно поднимать.

### 6.2. Host prerequisites для Android

На Ubuntu host должны быть установлены:

- `Java 17+`
- Android SDK command-line tools
- Android platform-tools
- Android emulator
- `adb`
- `sdkmanager`
- `avdmanager`
- `Appium 2`
- Appium driver `uiautomator2`

### 6.3. Android image

Для Ubuntu/x86_64 в production path использовать x86_64 system image.

Целевой override:

- `system-images;android-35;google_apis_playstore;x86_64`

Текущий `arm64-v8a` bootstrap из кода не считать production default для Ubuntu
server.

## 7. Целевой deploy root

Канонический путь:

```text
/opt/youtube_automation
```

Структура после внедрения:

```text
/opt/youtube_automation
├── backend/
├── frontend/
├── docs/
├── deploy/
│   ├── compose.prod.yml
│   ├── backend.env
│   ├── android.env
│   ├── gateway/
│   │   ├── Dockerfile
│   │   └── nginx.conf
│   └── systemd/
│       ├── yta-appium.service
│       ├── yta-android-worker.service
│       └── yta-android.target
├── ops/
│   ├── bootstrap-ubuntu.sh
│   ├── seed-runtime.sh
│   ├── up.sh
│   ├── down.sh
│   ├── status.sh
│   └── logs.sh
└── artifacts/
```

## 8. Целевой runtime split

### 8.1. Browser runtime

Desktop/session emulation в production должна идти через Playwright Chromium.

Обязательное решение:

- `APP__BROWSER_BACKEND=chromium`

AdsPower не является production baseline для unified Ubuntu deploy.

### 8.2. Android runtime

Android runtime должен использовать:

- host Appium server;
- host Android SDK/emulator tools;
- отдельный Android worker;
- отдельную очередь.

### 8.3. Analysis runtime

Post-processing analysis должен жить отдельно от Android и browser runtime.

## 9. Целевая схема очередей

Нужно зафиксировать три очереди:

- `taskiq_emulation` — browser runtime
- `taskiq_emulation_android` — Android runtime
- `taskiq_analysis` — ad analysis

Это обязательное изменение архитектуры.

Причины:

- Android и desktop должны масштабироваться независимо;
- Android job не должен случайно попасть в Playwright-only worker;
- падение Appium/emulator stack не должно ломать browser queue.

## 10. Целевая env-конфигурация

### 10.1. Container env

Файл:

```text
deploy/backend.env
```

Обязательные значения:

```dotenv
APP__ENV=prod
APP__BROWSER_BACKEND=chromium
APP__API__PORT=8000
APP__API__ALLOWED_HOSTS=["https://your-domain.example"]
APP__JWT__SECRET_KEY=<strong-secret>
APP__POSTGRES__HOST=postgres
APP__POSTGRES__PORT=5432
APP__POSTGRES__DB=app
APP__POSTGRES__USER=app
APP__POSTGRES__PASSWORD=<strong-password>
APP__REDIS__HOST=redis
APP__REDIS__PORT=6379
APP__REDIS__DB=0
TASKIQ_EMULATION_QUEUE_NAME=taskiq_emulation
TASKIQ_EMULATION_ANDROID_QUEUE_NAME=taskiq_emulation_android
TASKIQ_ANALYSIS_QUEUE_NAME=taskiq_analysis
APP__PLAYWRIGHT__HEADLESS=true
APP__PLAYWRIGHT__MAX_BROWSERS=2
APP__PLAYWRIGHT__CONTEXTS_PER_BROWSER=5
APP__GEMINI__API_KEY=<optional-or-empty>
```

### 10.2. Android host env

Файл:

```text
deploy/android.env
```

Целевые значения:

```dotenv
APP__ENV=prod
APP__BROWSER_BACKEND=chromium
APP__POSTGRES__HOST=127.0.0.1
APP__POSTGRES__PORT=55432
APP__POSTGRES__DB=app
APP__POSTGRES__USER=app
APP__POSTGRES__PASSWORD=<strong-password>
APP__REDIS__HOST=127.0.0.1
APP__REDIS__PORT=6379
APP__REDIS__DB=0
TASKIQ_QUEUE_NAME=taskiq_emulation_android
TASKIQ_EMULATION_QUEUE_NAME=taskiq_emulation
TASKIQ_EMULATION_ANDROID_QUEUE_NAME=taskiq_emulation_android
TASKIQ_ANALYSIS_QUEUE_NAME=taskiq_analysis
APP__ANDROID_APP__ENABLED=true
APP__ANDROID_APP__MANAGE_APPIUM_SERVER=false
APP__ANDROID_APP__APPIUM_SERVER_URL=http://127.0.0.1:4723
APP__ANDROID_APP__DEFAULT_AVD_NAME=yt_android_playstore_api35_x86_64
APP__ANDROID_APP__BOOTSTRAP_AVD_NAME=yt_android_playstore_api35_x86_64
APP__ANDROID_APP__BOOTSTRAP_SYSTEM_IMAGE_PACKAGE=system-images;android-35;google_apis_playstore;x86_64
APP__ANDROID_APP__BOOTSTRAP_DEVICE_PRESET=pixel_7
APP__ANDROID_APP__BOOTSTRAP_EMULATOR_GPU_MODE=swiftshader_indirect
APP__ANDROID_APP__EMULATOR_GPU_MODE=swiftshader_indirect
APP__ANDROID_APP__EMULATOR_USE_SNAPSHOTS=true
APP__ANDROID_APP__RUNTIME_SNAPSHOT_NAME=youtube_warm_updated
APP__ANDROID_APP__EMULATOR_HEADLESS=true
ANDROID_SDK_ROOT=/opt/android-sdk
ANDROID_HOME=/opt/android-sdk
JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
```

Примечание:

- `APP__ANDROID_APP__MANAGE_APPIUM_SERVER=false` является предпочтительным
  режимом для Ubuntu unified deploy;
- Appium должен управляться как отдельный systemd service, а не подниматься
  worker-ом по месту.

## 11. Целевой compose stack

Файл:

```text
deploy/compose.prod.yml
```

### 11.1. Сервисы core stack

- `gateway`
- `api`
- `migrator`
- `scheduler`
- `worker_browser`
- `worker_analysis`
- `postgres`
- `redis`

### 11.2. Что убрать из production core path

Из обязательного запуска исключить:

- `grafana`
- `loki`
- `prometheus`
- `promtail`
- VNC/noVNC ports
- прямую публикацию `postgres`
- прямую публикацию `redis`

Monitoring и debug-сервисы должны включаться только profile-ом.

### 11.4. Manual Android UI mode

Для ручного входа в Google/YouTube аккаунты, обновления приложения и проверки
экрана нужен отдельный on-demand режим, а не попытка подключиться к живому
headless runtime.

Целевой операторский сценарий:

```bash
./ops/android-ui.sh start
```

Что делает сценарий:

- останавливает `yta-android.target`, чтобы worker не держал AVD;
- поднимает отдельный visible Android emulator stack через:
  - `Xvfb`
  - `fluxbox`
  - `x11vnc`
  - `websockify/noVNC`
- печатает noVNC URL, по которому можно зайти в эмулятор из браузера;
- позволяет вручную войти в аккаунт и выполнить нужные действия.

После ручной настройки:

```bash
./ops/android-ui.sh save-snapshot
./ops/android-ui.sh stop
```

Где:

- `save-snapshot` сохраняет текущий warm snapshot;
- `stop` выключает manual UI stack и снова запускает обычный Android runtime.

Требования:

- manual UI не должен быть частью обязательного production core path;
- VNC/noVNC остаётся только on-demand функцией;
- для публичного доступа должен использоваться пароль
  `YTA_ANDROID_BOOTSTRAP_VNC_PASSWORD`.

### 11.3. Что изменить относительно текущего compose

Обязательные изменения:

- убрать bind mounts исходников;
- убрать `APP__ENV=dev`;
- не публиковать `db` и `redis` наружу;
- не публиковать debug/VNC порты;
- добавить отдельный production ingress;
- использовать env file из `deploy/backend.env`;
- оставить persistent volumes только для данных и артефактов;
- browser worker должен запускаться с Playwright image;
- analysis worker должен оставаться отдельно.

## 12. Целевой gateway

В production не нужен отдельный runtime frontend container.

Правильнее:

- собирать frontend в multi-stage image;
- конечный runtime держать в `gateway` image;
- `gateway` раздаёт `dist/` и проксирует API.

Преимущества:

- меньше контейнеров;
- меньше hop-ов;
- проще один публичный entrypoint;
- SPA и API живут под одним origin.

### 12.1. Требования к nginx

Должно быть:

- SPA fallback на `index.html`;
- proxy `/api` -> `api:8000`;
- отключение буферизации для SSE/status stream;
- long `proxy_read_timeout` для stream endpoints;
- gzip/static caching для frontend assets;
- TLS termination.

## 13. Целевой Android systemd stack

### 13.1. `yta-appium.service`

Ответственность:

- держать Appium доступным на `127.0.0.1:4723`;
- стартовать после загрузки системы;
- перезапускаться автоматически;
- использовать `deploy/android.env`.

### 13.2. `yta-android-worker.service`

Ответственность:

- запускать `taskiq worker` только для Android queue;
- работать из каталога `backend/`;
- использовать host Python env с `uv sync --extra android`;
- зависеть от `yta-appium.service`.

### 13.3. `yta-android.target`

Ответственность:

- объединять Android services в одну logical group;
- давать `ops/up.sh` и `ops/down.sh` один понятный systemd target.

## 14. Целевые ops scripts

### 14.1. `ops/bootstrap-ubuntu.sh`

One-time bootstrap:

- проверяет Ubuntu host;
- устанавливает Docker и Compose plugin;
- готовит директории;
- ставит Java и Android tooling;
- ставит Appium и `uiautomator2`;
- помогает создать AVD;
- разворачивает systemd unit files;
- выполняет первый `daemon-reload`.

### 14.2. `ops/seed-runtime.sh`

Post-deploy bootstrap для runtime-данных:

- idempotent create/update admin user;
- запись рабочих admin credentials в `deploy/admin.credentials`;
- idempotent upsert proxy entries, если переданы proxy env variables.

Целевой вызов:

```bash
YTA_ADMIN_USERNAME=admin \
YTA_ADMIN_PASSWORD='<strong-password>' \
YTA_PROXY_ID='<uuid>' \
YTA_PROXY_LABEL='Croatia Zagreb' \
YTA_PROXY_SCHEME='socks5' \
YTA_PROXY_HOST='157.22.93.107' \
YTA_PROXY_PORT='63273' \
YTA_PROXY_USERNAME='RHG2vKk4' \
YTA_PROXY_PASSWORD='NtkqQD3c' \
YTA_PROXY_COUNTRY_CODE='HR' \
YTA_PROXY_NOTES='IT HOSTLINE AS44559, Zagreb' \
./ops/seed-runtime.sh
```

### 14.3. `ops/up.sh`

Целевой порядок:

1. preflight checks;
2. `systemctl start yta-android.target`;
3. wait for Appium `/status`;
4. `docker compose -f deploy/compose.prod.yml up -d`;
5. wait for migrator completion;
6. wait for API `/ping`;
7. print final status.

### 14.4. `ops/down.sh`

Целевой порядок:

1. `docker compose -f deploy/compose.prod.yml down`;
2. `systemctl stop yta-android.target`;
3. показать остаточные процессы и статусы.

### 14.5. `ops/status.sh`

Должен показывать:

- docker compose services status;
- `systemctl status yta-appium`;
- `systemctl status yta-android-worker`;
- `adb devices`;
- API health;
- public URL reachability.

### 14.6. `ops/logs.sh`

Должен собирать в одном месте:

- `docker compose logs`;
- `journalctl -u yta-appium`;
- `journalctl -u yta-android-worker`.

## 15. Артефакты и хранение

Артефакты должны переживать пересоздание контейнеров.

Целевой подход:

- root path для runtime artifacts: `/opt/youtube_automation/artifacts`;
- backend containers и Android host worker используют один и тот же persistent
  storage path;
- cleanup должен быть отдельной осознанной операцией, не частью `down.sh`.

## 16. Безопасность и production hygiene

Обязательные правила:

- не коммитить production secrets;
- не использовать `your-secret-key-here`;
- не публиковать `postgres`, `redis`, `grafana`, `vnc`, `novnc` наружу;
- не использовать `APP__ENV=dev` в production;
- ограничить `APP__API__ALLOWED_HOSTS`;
- включить HTTPS через `gateway`;
- логины/пароли Grafana не считать частью core deploy path.

## 17. Критические расхождения текущего репозитория с целевой схемой

Ниже перечислено то, что нужно изменить, прежде чем unified Ubuntu deploy можно
считать готовым.

### 17.1. Очереди

Сейчас Android и desktop по факту не разведены как целевая топология.

Нужно:

- добавить отдельный `TASKIQ_EMULATION_ANDROID_QUEUE_NAME`;
- роутить Android tasks только в Android queue;
- запускать Android worker только на этой queue.

### 17.2. Browser backend default

Сейчас default backend в коде — `adspower`.

Нужно:

- продовый baseline сделать `chromium`;
- AdsPower считать необязательным интеграционным режимом, а не core path.

### 17.3. Frontend production path

Сейчас нет production gateway/static serving как канонической схемы.

Нужно:

- собрать frontend в runtime-ready gateway image;
- перестать считать `vite preview` production-вариантом.

### 17.4. Android Ubuntu bootstrap

Текущие helper scripts ориентированы в основном на macOS.

Нужно:

- завести отдельный Ubuntu bootstrap path;
- не использовать arm64 bootstrap image как server default.

### 17.5. Test gate

Сейчас тесты backend не являются надёжным deploy gate.

Нужно:

- починить текущие падающие тесты;
- либо перевести integration path на Postgres-backed test setup;
- только потом использовать CI как реальный стоп-кран перед выкладкой.

## 18. Целевые acceptance criteria

Unified Ubuntu deploy считается готовым, когда выполняются все условия ниже.

### 18.1. Deploy UX

- `./ops/up.sh` поднимает весь стек без ручных шагов;
- `./ops/down.sh` корректно всё останавливает;
- `./ops/status.sh` даёт понятный consolidated health report.

### 18.2. Web/API

- frontend доступен по публичному домену;
- `/api/ping` работает через gateway;
- SPA routes открываются напрямую без 404;
- SSE/status stream не ломается за reverse proxy.

### 18.3. Browser runtime

- desktop session стартует из UI;
- задача уходит в browser queue;
- Playwright worker успешно её отрабатывает.

### 18.4. Android runtime

- Android session стартует из UI;
- задача уходит в Android queue;
- Android worker поднимает или использует AVD;
- Appium session создаётся стабильно;
- результат сохраняется в ту же систему истории и артефактов.

### 18.5. Hardening

- secrets вынесены в env files;
- debug ports не опубликованы наружу;
- данные и артефакты переживают `up/down`;
- monitoring не является обязательной частью core path.

## 19. Порядок внедрения

Ниже зафиксирован правильный порядок работ. Его и использовать дальше.

### Phase 1. Production foundation

Сделать:

- `deploy/compose.prod.yml`
- `deploy/backend.env.example`
- production gateway image
- `ops/up.sh`, `down.sh`, `status.sh`, `logs.sh`

Результат:

- web + api + postgres + redis + browser worker + analysis worker стартуют одной
  командой.

### Phase 2. Queue split

Сделать:

- отдельную Android queue;
- отдельную маршрутизацию Android tasks;
- отдельный Android worker command path.

Результат:

- Android задачи больше не попадают в browser runtime.

### Phase 3. Ubuntu Android bootstrap

Сделать:

- `ops/bootstrap-ubuntu.sh`
- `deploy/android.env.example`
- `deploy/systemd/*.service`
- Ubuntu preflight checks

Результат:

- Android host runtime разворачивается предсказуемо.

### Phase 4. Unified start

Сделать:

- запуск Android target из `ops/up.sh`;
- wait logic для Appium и compose health;
- consolidated status output.

Результат:

- весь стек реально поднимается одной командой.

### Phase 5. Hardening and smoke tests

Сделать:

- smoke suite после `up.sh`;
- health/assertions;
- корректные secrets;
- monitoring как optional profile.

Результат:

- стек не просто поднимается, а даёт проверяемый ready state.

## 20. Что считать нецелевым решением

Не делать:

- Android emulator как обязательный сервис внутри production compose;
- `vite preview` как production frontend;
- одну общую queue для browser и Android;
- AdsPower как обязательную зависимость production baseline;
- обязательный запуск Grafana/Loki/Prometheus для core deploy;
- публикацию внутренних сервисов наружу ради "простоты".

## 21. Финальное решение

Каноническая production стратегия для Ubuntu:

- единый пользовательский запуск через `./ops/up.sh`;
- контейнерный core stack через `docker compose`;
- Android runtime через host `systemd`;
- frontend как static build внутри `gateway`;
- desktop runtime через Playwright Chromium;
- Android runtime через Appium + AVD на host;
- три отдельные очереди: browser, android, analysis.

Это и есть целевая модель, которой нужно придерживаться в следующих шагах
внедрения.
