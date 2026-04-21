# Android Native YouTube Runtime Plan

## 1. Цель

Добавить в текущую систему **второй runtime** для эмуляции YouTube через **нативное Android-приложение**, не ломая текущий browser/web runtime на `Playwright + AdsPower`.

Цель не в том, чтобы "улучшить мобильный web". Цель в том, чтобы получить отдельный, устойчивый путь:

- `Android Emulator / AVD`
- `Appium`
- `UiAutomator2`
- `YouTube app`

с переиспользованием уже существующих слоев:

- API
- orchestration
- session store
- history/persistence
- ad analysis pipeline
- frontend contracts

## 2. Ключевое решение

### 2.1. Не отдельный проект

Это должно жить **внутри текущего репозитория**, потому что:

- control plane уже есть;
- очереди и orchestration уже есть;
- Redis session store уже есть;
- persistence/history уже есть;
- frontend уже понимает модель сессии;
- ad analysis и media pipeline уже есть.

Отдельный проект дал бы:

- второй API;
- второй session/history contract;
- рассинхрон моделей;
- лишнюю интеграционную сложность.

### 2.2. Но это и не расширение текущего browser runtime

Нельзя пытаться встроить native app runtime внутрь существующего `Playwright`-слоя.

Причина: текущий runtime жестко завязан на:

- `BrowserSessionProvider`
- `BrowserContext`
- `Page`
- DOM/CSS selectors
- browser-only navigation model

Это видно в существующих точках:

- [backend/src/app/services/browser/provider.py](../backend/src/app/services/browser/provider.py)
- [backend/src/app/services/emulation/runtime.py](../backend/src/app/services/emulation/runtime.py)
- [backend/src/app/services/emulation/run.py](../backend/src/app/services/emulation/run.py)
- [backend/src/app/tasks/emulation.py](../backend/src/app/tasks/emulation.py)

Правильный подход:

- оставить browser runtime как есть;
- добавить **parallel runtime branch** для Android app;
- переиспользовать только верхние слои системы.

## 3. Что уже есть в проекте

### 3.1. Что можно переиспользовать без радикальной переписи

#### API / control plane

- [backend/src/app/api/modules/emulation/service.py](../backend/src/app/api/modules/emulation/service.py)
- [backend/src/app/api/modules/emulation/routes.py](../backend/src/app/api/modules/emulation/routes.py)
- [backend/src/app/api/modules/emulation/schema.py](../backend/src/app/api/modules/emulation/schema.py)

Это уже умеет:

- стартовать сессию;
- останавливать;
- retry/resume;
- отдавать status/detail/history/dashboard.

#### Очереди и scheduler

- [backend/src/app/tiq.py](../backend/src/app/tiq.py)
- [backend/src/app/tasks/emulation.py](../backend/src/app/tasks/emulation.py)
- [backend/src/app/services/emulation/orchestration/scheduler.py](../backend/src/app/services/emulation/orchestration/scheduler.py)

Это уже дает:

- TaskIQ queue model;
- отложенные resume/chunk runs;
- разделение очередей по типу workload.

#### Session store / live state

- [backend/src/app/services/emulation/session/store.py](../backend/src/app/services/emulation/session/store.py)
- [backend/src/app/services/emulation/session/state.py](../backend/src/app/services/emulation/session/state.py)

Это уже дает:

- live payload в Redis;
- locking;
- orchestration state;
- storage watched videos / ads / progress.

#### Persistence / history

- [backend/src/app/services/emulation/persistence/history.py](../backend/src/app/services/emulation/persistence/history.py)
- [backend/src/app/services/emulation/persistence/captures.py](../backend/src/app/services/emulation/persistence/captures.py)

#### Post-processing / ad analysis

- [backend/src/app/tasks/ad_analysis.py](../backend/src/app/tasks/ad_analysis.py)
- [backend/src/app/services/emulation/ad_analysis.py](../backend/src/app/services/emulation/ad_analysis.py)

### 3.2. Что переиспользовать нельзя напрямую

Нужно писать заново:

- `navigator`
- `searcher`
- `watcher`
- `playback`
- `engagement`
- `ads handler`
- runtime provider

Потому что native app не имеет:

- DOM;
- CSS selectors;
- `Page`;
- browser URL flow как главную модель работы.

## 4. Почему AdsPower здесь не решает задачу

AdsPower подходит для:

- browser profile;
- browser proxy;
- browser fingerprint;
- cookies / localStorage;
- web automation.

AdsPower **не является рантаймом нативного YouTube app**.

Практический вывод:

- browser profile из AdsPower нельзя "перенести" в YouTube app;
- browser cookies нельзя использовать как app session;
- browser fingerprint не равен Android device profile.

Что из AdsPower можно взять:

- proxy
- timezone
- locale
- language
- region
- device class как ориентир

То есть AdsPower profile используется только как **template source**, а не как runtime container.

## 5. Рекомендуемый стек

### 5.1. Android first

Первым делаем **Android**, не iOS.

Причины:

- Appium + Android проще и стабильнее;
- `UiAutomator2` mature;
- AVD проще автоматизировать и воспроизводить;
- iOS резко усложняет инфраструктуру и отладку.

### 5.2. Базовый стек

- **Android Emulator / AVD**
- **Appium 2**
- **UiAutomator2 driver**
- **ADB**
- **YouTube Android app**

### 5.3. Почему именно так

#### Appium

Appium нужен как orchestration layer для native UI automation.

Он дает:

- единый remote driver interface;
- session lifecycle;
- команды поиска и взаимодействия с UI;
- возможность масштабировать под несколько девайсов.

#### UiAutomator2

Это Android automation engine внутри Appium.

Для Android это правильный путь, потому что:

- это стандартный Appium-драйвер;
- он понимает native Android UI tree;
- поддерживает `resource-id`, `accessibility id`, текст и иерархию view.

#### AVD

`AVD` = `Android Virtual Device`.

Это правильный аналог AdsPower profile для native app мира:

- отдельное виртуальное устройство;
- отдельное app state;
- отдельный аккаунт;
- отдельный proxy и окружение;
- отдельные snapshots.

## 6. Официальные источники по стеку

Использовать как базовые технические reference:

- Appium Quickstart:
  - https://appium.io/docs/en/2.18/quickstart/
- Appium UiAutomator2 Quickstart:
  - https://appium.io/docs/en/2.1/quickstart/uiauto2-driver/
- Android Emulator networking / proxy:
  - https://developer.android.com/studio/run/emulator-networking-proxy
- Android `avdmanager`:
  - https://developer.android.com/studio/command-line/avdmanager
- Android `sdkmanager`:
  - https://developer.android.com/studio/command-line/sdkmanager
- AdsPower Local API:
  - https://help.adspower.com/docs/api
- AdsPower “Run UI automation tools” note:
  - https://www.adspower.com/news/what-is-new-what-we-launched-in-february-2024

## 7. Архитектурные принципы

### 7.1. Ports and Adapters

Нужно четко разделить:

- **control plane**
- **runtime provider**
- **platform automation**
- **domain/session state**

Это значит:

- API и orchestration не должны знать про `Playwright` или `Appium`;
- runtime должен предоставлять одинаковую доменную семантику:
  - search happened
  - watch started
  - watch completed
  - ad detected
  - landing opened
  - session finalized

### 7.2. Separate Runtime Strategy

Не нужен единый "mega runtime" на все платформы.

Нужны два отдельных runtime path:

- `browser_web`
- `android_app`

с общей доменной моделью результата.

### 7.3. Preserve Working Desktop Path

Нельзя ломать текущий desktop/macOS/browser path.

Следствие:

- никаких глобальных абстракций ради абстракций;
- никакого forced unification на уровне `navigator/searcher/watcher`;
- browser runtime остается отдельным и рабочим baseline.

### 7.4. Infrastructure Before Behavior

Сначала:

- AVD lifecycle;
- Appium session;
- открытие app;
- базовый watch flow.

Потом:

- comments;
- like/subscribe;
- ads;
- parity polish.

Не наоборот.

## 8. Целевая структура кода

```text
backend/src/app/services/mobile_app/
  __init__.py
  models.py
  profiles/
    __init__.py
    template.py
    mapper.py
  android/
    __init__.py
    avd_manager.py
    appium_provider.py
    runtime.py
    runner.py
    errors.py
    youtube/
      __init__.py
      selectors.py
      navigator.py
      searcher.py
      playback.py
      watcher.py
      engagement.py
      ads.py
      recovery.py
```

### Почему именно так

- `profiles/` отделяет template-level concerns от device runtime;
- `android/` отделяет infra от YouTube app logic;
- `youtube/` отделяет platform-specific app flow от infra bootstrapping;
- `recovery.py` не смешивается с watcher до состояния god-file;
- `models.py` может хранить runtime-type enum и app-specific small models.

## 8.1. Паттерны проектирования, которые тут действительно нужны

### Adapter

Нужен для интеграции двух разных automation backends в один control plane:

- browser runtime -> доменная модель сессии;
- Android runtime -> та же доменная модель сессии.

Это дает возможность не размазывать знание `Playwright` и `Appium` по API и orchestration.

### Factory / Provider

Уместно использовать для:

- lifecycle session provider;
- runtime assembly;
- template-to-runtime config building.

Это естественно продолжает текущий стиль с `BrowserSessionProvider`, но не тащит browser-типы в Android path.

### State Machine

Обязательна для:

- player recovery;
- ad detection;
- landing flow return path.

Без явной state machine mobile app runtime быстро деградирует в набор случайных `if/else`.

### Anti-Corruption Layer

Нужна между:

- AdsPower profile metadata
- AndroidDeviceProfileTemplate

Именно этот слой защищает Android runtime от утечки browser-specific assumptions.

### Repository reuse

Android runtime не должен лезть в БД напрямую.

Надо переиспользовать:

- `EmulationSessionStore`
- `EmulationPersistenceService`
- history services/gateway

Это сохраняет один источник истины для live state и history.

## 8.2. Внутренние интерфейсы, которые стоит зафиксировать заранее

Ниже не финальный синтаксис, а границы ответственности.

### `AndroidDeviceManager`

Отвечает за:

- start/stop AVD;
- restore snapshot;
- wait for boot;
- expose `adb_serial`.

Не должен:

- знать про topics;
- знать про YouTube app behavior;
- писать session state.

### `AndroidSessionProvider`

Отвечает за:

- create Appium session;
- open target app;
- return driver handle;
- teardown session.

Не должен:

- решать, что смотреть;
- выполнять domain workflow;
- знать про persistence.

### `AndroidYouTubeRuntime`

Отвечает за:

- open/search/watch/recover;
- взаимодействие с app UI;
- публикацию результата в domain state.

Не должен:

- работать с очередью;
- знать про HTTP API;
- принимать orchestration decisions следующего чанка.

### `AndroidRunService`

Отвечает за:

- связать provider + runtime + store + persistence + orchestration;
- выполнить один runtime-run/chunk;
- финализировать результат.

Это Android-аналог `EmulationRunService`, но без browser-only типов.

## 8.3. Порты, которые стоит держать малыми

### `DeviceRuntimePort`

Lifecycle:

- `prepare()`
- `acquire()`
- `release()`
- `dispose()`

### `VideoPlatformRuntimePort`

Scenario actions:

- `open_home()`
- `search(topic)`
- `open_video(candidate)`
- `watch_current(target_seconds)`
- `recover_watch()`
- `capture_ad_if_present()`

### `TemplateResolverPort`

Template actions:

- `resolve_device_template(device_profile_id)`
- `build_runtime_config(template)`

### Важное уточнение про `VideoPlatformRuntimePort`

На этом этапе это **не общий порт для browser и Android runtime**.

Это внутренний порт **только для Android app branch**.

Причина:

- browser runtime уже рабочий;
- forcing browser path реализовывать новый общий runtime contract создаст большой рефактор без бизнес-выигрыша;
- это нарушит принцип `Preserve Working Desktop Path`.

Следствие:

- browser path пока остается на своем текущем внутреннем runtime API;
- Android path строится отдельно;
- унификация допустима только позже и только если появится реальная повторяемая доменная польза.

## 9. Изменения в control plane

### 9.1. Новый runtime type

Нужен явный runtime discriminator:

- `browser_web`
- `android_app`

Он должен появиться:

- в start request;
- в history model;
- в live status;
- в persistence.

### 9.2. Новый device profile identifier

Текущий `profile_id` сейчас означает AdsPower profile id.

Для Android нужен отдельный идентификатор, например:

- `device_profile_id`

Потому что:

- browser profile и Android device template не одно и то же;
- иначе контракт станет двусмысленным.

### 9.3. Почему не стоит переиспользовать `profile_id`

Если оставить одно поле:

- часть кода будет ожидать AdsPower id;
- часть кода будет ожидать Android device template id;
- ошибки станут неочевидными.

Правильнее:

- `profile_id` для browser path;
- `device_profile_id` для Android app path.

## 10. Новые доменные сущности

### 10.1. AndroidDeviceProfileTemplate

Минимальные поля:

- `template_id`
- `source_adspower_profile_id`
- `proxy_url`
- `timezone`
- `locale`
- `language`
- `geo`
- `device_preset`
- `youtube_account_label`
- `notes`

### 10.2. AndroidRuntimeConfig

Поля:

- `avd_name`
- `appium_server_url`
- `youtube_package`
- `youtube_activity`
- `adb_serial`
- `proxy_applied`
- `snapshot_name`

### 10.3. EmulationRuntimeType

Enum:

- `BROWSER_WEB`
- `ANDROID_APP`

### 10.4. AndroidSessionResult normalization

Android runtime должен приводить результат к той же доменной форме, что и browser runtime.

Минимальный нормализованный результат:

- `topics_searched`
- `watched_videos`
- `current_watch`
- `watched_ads`
- `mode`
- `fatigue`
- `error`
- `orchestration payload updates`

Это нужно, чтобы:

- frontend не знал о внутреннем runtime;
- history/detail/status не превращались в два отдельных API мира.

### 10.5. Browser-specific metrics policy

Есть поля, которые сейчас естественно принадлежат browser runtime, например:

- `bytes_downloaded`

Для Android MVP нужно заранее зафиксировать поведение:

- Android runtime на первом этапе пишет `bytes_downloaded = 0`
- это считается осознанной временной деградацией метрики, а не багом

Почему так:

- надежный источник сетевого трафика для AVD/Appium path не появится бесплатно;
- придумывать фальшивую метрику хуже, чем честно писать `0`

Если потом понадобится parity:

- вводится отдельный Android-specific способ учета;
- только после этого обсуждается унификация метрики.

### 10.6. `surface_mode` остается browser-only

Текущий `surface_mode` относится к browser/web миру:

- `desktop`
- `mobile web`

Для Android native runtime это **неправильная абстракция**.

Нельзя делать:

- `SurfaceMode.ANDROID`

Правильная граница:

- `surface_mode` остается browser-only concept;
- Android определяется через `runtime_type = android_app`, а не через surface.

## 11. Где менять текущий backend

### 11.1. API

Изменения:

- [backend/src/app/api/modules/emulation/schema.py](../backend/src/app/api/modules/emulation/schema.py)
- [backend/src/app/api/modules/emulation/service.py](../backend/src/app/api/modules/emulation/service.py)
- [backend/src/app/api/modules/emulation/models.py](../backend/src/app/api/modules/emulation/models.py)
- [backend/src/app/api/modules/emulation/gateway.py](../backend/src/app/api/modules/emulation/gateway.py)

Что добавить:

- `runtime_type`
- `device_profile_id`
- отображение runtime type в history/detail/status

### 11.2. Очереди

Изменения:

- [backend/src/app/tiq.py](../backend/src/app/tiq.py)
- [backend/src/app/tasks/emulation.py](../backend/src/app/tasks/emulation.py)
- [backend/compose.yml](../backend/compose.yml)

Что добавить:

- отдельную очередь для Android:
  - `taskiq_emulation_android`
- отдельный worker

Причина:

- Android runtime будет тяжелее;
- у него другой lifecycle;
- ему не место в том же процессе, что и browser worker.

### 11.3. Run service

Нельзя просто расширить текущий [backend/src/app/services/emulation/run.py](../backend/src/app/services/emulation/run.py) условием `if runtime_type == ...`.

Причина:

- этот класс уже живет в browser assumptions;
- он принимает `BrowserSessionProvider`, `BrowserContext`, `Page`.

Нужен новый сервис, например:

- `AndroidAppRunService`

И отдельный task:

- `emulation_android_task`

## 12. Инфраструктурные решения

### 12.1. Где запускать emulator на первом этапе

На первом этапе **не Docker**.

Причина:

- nested virtualization;
- нестабильность Android Emulator внутри контейнеров;
- лишний риск на старте;
- сложнее работать на macOS.

Правильнее:

- emulator и Appium запускать на host;
- backend worker может общаться с ними как с внешними сервисами.

### 12.2. Docker позже

Dockerization уместна только потом, и скорее как Linux-only path.

На начальном этапе это не должно блокировать MVP.

### 12.3. Proxy

Основной вариант:

- `emulator -http-proxy ...`

Преимущества:

- proxy применяется на уровне emulator networking;
- ближе к "proxy per profile";
- проще, чем пытаться полагаться на browser-only config.

Не надо на первом этапе:

- MITM HTTPS interception;
- сложные сертификаты;
- traffic rewriting.

### 12.4. Snapshots

Нужны минимум два snapshot-понятия:

- **base snapshot**
  - чистое устройство, app установлено, без аккаунта
- **warm snapshot**
  - YouTube app и аккаунт уже готовы, базовый прогрев выполнен

Без этого AVD startup/login cost будет слишком большим.

### 12.5. Почему нужен отдельный Appium server

Appium server должен жить как отдельный runtime dependency.

Причины:

- понятный lifecycle;
- проще дебажить hanging sessions;
- проще отделить проблемы backend worker от проблем emulator/device;
- проще масштабировать на несколько устройств.

### 12.6. Почему host-run AVD лучше, чем сразу "все в compose"

Проект уже использует compose для backend-инфраструктуры, но Android emulator имеет другую operational природу:

- ему нужна аппаратная виртуализация;
- он чувствителен к host OS;
- путь на macOS и Linux различается.

Поэтому на первом этапе правильнее:

- backend поднимать как сейчас;
- emulator/Appium поднимать отдельно;
- связывать их по network endpoints и lifecycle scripts.

## 13. Использование AdsPower profile как шаблона

### 13.1. Что переносим

- proxy
- timezone
- locale
- language
- region
- device class as approximation

### 13.2. Что не переносим

- cookies
- localStorage
- browser cache
- browser fingerprint
- browser extensions
- browser session

### 13.3. Практический смысл

AdsPower profile становится **input artifact** для Android template creation, а не runtime dependency.

## 14. Milestones

## Milestone 0. Architecture Freeze

### Scope

- Зафиксировать целевой стек.
- Зафиксировать границы между browser runtime и Android runtime.
- Зафиксировать naming и contracts.

### Deliverables

- runtime enum;
- решение по `device_profile_id`;
- решение по очередям;
- design doc утвержден.

### Acceptance Criteria

- нет двусмысленности, что такое `profile_id`;
- нет двусмысленности, где живет Appium runtime;
- есть утвержденная структура модулей.

### Risks

- premature abstraction;
- попытка "унифицировать все" до первого рабочего Android MVP;
- слишком широкий control-plane refactor вокруг `runtime_type`, который случайно меняет поведение рабочего browser path.

### Дополнительное ограничение Milestone 0

Любые изменения `runtime_type` в:

- schema
- service
- models
- gateway
- persistence/history mapping

допустимы только при условии, что:

- browser runtime не меняет поведение;
- текущие browser session flows не требуют миграции logic;
- frontend продолжает нормально работать со старыми browser sessions.

## Milestone 1. Android Toolchain Bootstrap

### Scope

- Android SDK
- `sdkmanager`
- `avdmanager`
- emulator
- adb
- Appium 2
- UiAutomator2

### Deliverables

- локальный bootstrap script;
- базовая инструкция для dev env;
- один AVD поднимается командой.

### Acceptance Criteria

- `adb devices` видит девайс;
- YouTube app можно открыть вручную;
- Appium session устанавливается.

### Risks

- virtualization issues;
- несовместимость с конкретным host setup;
- нестабильный cold boot.

## Milestone 2. Android Device Profile Template

### Scope

- сущность template;
- mapper из AdsPower profile metadata;
- хранение template.

### Deliverables

- модель template;
- сервис загрузки template;
- минимальный CRUD или конфиг-файл storage.

### Acceptance Criteria

- можно создать template по образцу `k1aynhkb`;
- template содержит proxy/timezone/locale/language/device preset.

### Risks

- попытка тащить слишком много из AdsPower;
- смешение browser и app профилей.

## Milestone 2.5. Warm Snapshot Lifecycle

### Scope

- аккаунтный onboarding;
- базовый app warmup;
- сохранение warm snapshot;
- политика обновления/протухания snapshots.

### Deliverables

- documented warmup flow;
- snapshot naming convention;
- recovery policy при протухшем snapshot.

### Acceptance Criteria

- можно поднять warm AVD snapshot и быстро попасть в рабочее состояние YouTube app;
- после рестарта раннера не требуется каждый раз вручную логинить и прогревать устройство.

### Risks

- security challenge на аккаунте;
- изменившийся onboarding;
- snapshot invalidation после app update;
- скрытая зависимость качества рантайма от ручного warmup.

## Milestone 3. AVD Lifecycle Manager

### Scope

- create/start/stop AVD;
- boot readiness;
- snapshot restore;
- proxy application.

### Deliverables

- `avd_manager.py`
- lifecycle API
- error classification

### Acceptance Criteria

- AVD reliably starts and becomes usable;
- при падении эмулятора это детектируется;
- proxy применяется предсказуемо.

### Risks

- zombie emulators;
- stale adb devices;
- snapshot corruption.

## Milestone 4. Appium Session Provider

### Scope

- создать Appium driver session;
- открыть YouTube app;
- teardown session.

### Deliverables

- `appium_provider.py`
- provider interface
- retry policy на session boot

### Acceptance Criteria

- worker может получить live driver;
- при ошибке session setup корректно освобождаются ресурсы.

### Risks

- hanging Appium sessions;
- emulator alive, appium dead;
- UiAutomator2 init timeouts.

## Milestone 5. Android MVP Runtime

### Scope

- открыть app;
- обработать popups;
- search topic;
- открыть видео;
- смотреть 60-120 секунд;
- завершить session chunk.

### Deliverables

- `youtube/navigator.py`
- `youtube/searcher.py`
- `youtube/playback.py`
- `youtube/watcher.py`
- `android/runtime.py`
- `android/runner.py`

### Acceptance Criteria

- `10-15m` session проходит end-to-end;
- watched video пишется в live status и history;
- basic recovery работает.

### Risks

- нестабильность accessibility tree;
- плавающие onboarding/popups;
- app update changing selectors.

## Milestone 6. Reuse Existing Session and Persistence Layers

### Scope

- подключить Redis session store;
- persistence/history;
- scheduler/orchestration concepts.

### Deliverables

- Android run service пишет в те же models;
- history/detail/status видят Android sessions.

### Acceptance Criteria

- frontend показывает Android session без отдельного UI;
- resume/retry conceptually работает.

### Risks

- browser-only assumptions в persistence;
- несоответствие fields вроде `bytes_downloaded`.

## Milestone 7. Interaction Parity

### Scope

- comments glance;
- like;
- subscribe;
- continue current video;
- topic rotation.

### Deliverables

- `youtube/engagement.py`
- richer watcher behavior

### Acceptance Criteria

- Android app path ведет себя пользовательски правдоподобно;
- результаты отражаются в live/detail payload.

### Risks

- UI differences by account state or experiment bucket;
- flakiness of long-tap/swipe gestures.

## Milestone 8. Ad Detection MVP

### Scope

- ad badge detection;
- skip button;
- CTA presence;
- basic ad event recording.

### Deliverables

- `youtube/ads.py`
- first ad-detection state machine

### Acceptance Criteria

- хотя бы один ad event фиксируется корректно;
- false positives приемлемо ограничены.

### Risks

- native YouTube ad UI непостоянен;
- accessibility labels могут быть нестабильны;
- часть ad UI придется детектить OCR/vision-эвристиками.

## Milestone 9. Ad Capture and Landing Flow

### Scope

- screenshots;
- CTA tap;
- landing URL detection;
- возврат в app.

### Deliverables

- basic capture pipeline for Android app ads;
- handoff в текущий ad-analysis pipeline.

### MVP Boundary

На первом проходе `Milestone 9` покрывает только **один landing scenario**:

- `Chrome Custom Tab`

Не входит в первый MVP этого milestone:

- system browser handoff;
- app intent open.

### Acceptance Criteria

- ad path проходит `detect -> capture -> landing -> analysis`.

### Risks

- external browser handoff complexity;
- Android intent handling;
- landing capture не всегда можно унифицировать с web capture.

## Milestone 10. Stability, Snapshots, Operations

### Scope

- warmed snapshots;
- crash recovery;
- queue isolation;
- watchdogs;
- restart strategy.

### Deliverables

- operational runbook;
- stable long-run behavior;
- per-profile lifecycle discipline.

### Acceptance Criteria

- несколько длинных Android sessions подряд без ручного ремонта;
- stuck emulator/appium recovery работает автоматически.

### Risks

- snapshot drift;
- account/account-security prompts;
- device storage bloat over time.

## 15. Как реализовать runtime без архитектурного мусора

### 15.1. Не тянуть Appium в browser code

Нельзя:

- добавлять `if android_app` в `browser/provider.py`;
- добавлять `if appium` в `emulation/browser/*`;
- переиспользовать browser navigator для app.

Это даст хрупкий код и быстро превратится в god-runtime.

### 15.2. Нужен небольшой общий контракт выполнения

Нужен простой internal contract уровня:

- `run(session_id, duration_minutes, topics, runtime_config) -> result`

Снаружи orchestration и task layer могут быть общими, но runtime implementation должен быть отдельным.

### 15.3. SessionState остается общей

`SessionState` и store-model можно переиспользовать как доменную модель, если:

- не тянуть туда `Page`/`Driver`;
- не пришивать runtime-specific flags слишком глубоко.

## 16. Очереди и workers

### Целевая схема

- `taskiq_emulation` — browser runtime
- `taskiq_emulation_android` — Android app runtime
- `taskiq_analysis` — ad analysis

### Почему это важно

- Android emulator sessions тяжелее;
- browser worker и Android worker должны скейлиться отдельно;
- падение Appium/emulator stack не должно влиять на browser queue.

### Предлагаемая топология процессов

#### Backend API

Отвечает за:

- start/stop/retry/resume;
- history/status/detail;
- enqueue задач.

#### Browser worker

Отвечает только за:

- `browser_web` runtime.

#### Android worker

Отвечает только за:

- `android_app` runtime;
- работу с AVD manager и Appium provider.

#### Analysis worker

Отвечает только за:

- post-processing ad analysis.

## 17. Compose / deployment стратегия

### Этап 1

Не добавлять Android emulator в `backend/compose.yml` как обязательный сервис.

Правильнее:

- backend + redis + postgres + API в compose;
- Appium + emulator поднимать отдельно на host.

### Этап 2

Когда MVP стабилен:

- добавить optional профиль или отдельный compose override для Linux runner;
- не делать это блокером архитектуры.

## 18. Тестовая стратегия

### Unit tests

Покрыть:

- template mapping;
- runtime config building;
- queue/task routing;
- session result normalization;
- recovery state machine.

### Integration tests

На первом этапе:

- fake Appium driver;
- mocked AVD manager;
- full backend flow without real emulator.

### E2E

Обязательные сценарии:

1. open app -> search -> open video -> watch -> finalize
2. crash during watch -> recover
3. stop session
4. resume session
5. long session with chunk orchestration

### Contract tests

Отдельно нужны contract tests для проверки, что:

- browser runtime и android runtime возвращают одинаково нормализованные структуры;
- history/detail/status не ломаются от нового runtime type;
- frontend не получает неожиданной семантики.

### Ad E2E

Отдельный набор:

1. ad appears
2. skip button visible
3. CTA tap
4. landing captured
5. analysis queued

## 19. Нефункциональные требования

### Maintainability

- никакого смешения browser/runtime code;
- modules small and responsibility-driven;
- recovery logic не встраивать хаотично в watcher.

### Operability

- каждый crash должен быть классифицирован:
  - AVD boot failure
  - Appium session init failure
  - app launch failure
  - watch failure
  - ad handling failure

### Observability

Нужны structured logs с обязательными полями:

- `runtime_type`
- `device_profile_id`
- `avd_name`
- `adb_serial`
- `session_id`
- `phase`
- `video_id`
- `topic`

## 20. Нюансы по самой эмуляции в Android emulator

### 20.1. Search surface

Надо ожидать:

- onboarding;
- regional prompts;
- consent screens;
- search suggestions;
- voice/search permission popups.

Следовательно navigator должен иметь:

- popup dismiss layer;
- idempotent `ensure_home()`;
- safe `ensure_search_open()`.

### 20.2. Watch surface

Надо учитывать:

- mini-player;
- fullscreen transitions;
- autoplay;
- gesture conflicts;
- intermittent native overlays.

### 20.3. Comments

Comments в native app надо делать как отдельный gesture flow:

- swipe to comments region;
- short read;
- return to player;
- no assumption about fixed DOM anchors.

### 20.4. Ads

Ads в native app нельзя проектировать как DOM-driven logic.

Нужна state machine по сигналам:

- ad badge visible;
- skip button visible;
- sponsor/CTA labels;
- timer;
- external intent open.

### 20.5. Landing capture

Для native app landing flow сложнее, чем в web:

- CTA может открыть Chrome Custom Tab;
- может открыть системный browser;
- может открыть app intent.

Следовательно landing capture нужен в отдельном адаптере, а не внутри ad detector.

### 20.6. Аккаунт и прогрев

Для Android app profile нельзя считать устройство готовым, если:

- YouTube app только что установлен;
- не пройден onboarding;
- не выполнен базовый прогрев;
- аккаунт не зафиксирован в рабочем состоянии.

Нужен отдельный lifecycle:

1. создать template;
2. поднять AVD;
3. залогинить аккаунт;
4. прогреть app surface;
5. сохранить warm snapshot;
6. только после этого использовать профиль для production-like runs.

### 20.7. Selector policy для native app

Порядок предпочтения должен быть таким:

1. `resource-id`
2. `accessibility id`
3. stable text
4. xpath
5. OCR/vision fallback

Почему:

- `xpath everywhere` быстро станет медленным и хрупким;
- OCR должен быть только fallback, а не основной механизм управления.

## 21. Лучшие технологии под задачу

### Рекомендуется

- `Appium 2`
- `UiAutomator2`
- `Android Emulator / AVD`
- `ADB`
- Python `appium-python-client`
- host-run emulator/Appium for MVP

### Не рекомендуется как основной путь

- iOS first
- native app через AdsPower
- browser cookies migration into app
- Docker-first emulator strategy
- OCR-first whole-runtime automation

### OCR / vision где уместны

Только как secondary tool:

- для ad UI fallback;
- для text verification;
- для нестабильных элементов без accessibility identifiers.

Но не как основной control mechanism.

## 22. Что делать первым

Самый правильный первый реальный deliverable:

1. создать Android template по образцу `k1aynhkb`;
2. поднять AVD с proxy/timezone/locale;
3. поднять Appium session;
4. открыть YouTube app;
5. выполнить один search;
6. открыть одно видео;
7. смотреть 60-120 секунд;
8. записать результат в текущий session/history contract.

Пока это не сделано, нельзя идти:

- в ad parity;
- в like/subscribe parity;
- в сложную orchestration polish.

## 22.1. Полезный предварительный spike

До полноценной реализации допустим один короткий технический spike:

- вручную поднять один AVD;
- вручную открыть YouTube app через Appium;
- подтвердить:
  - что search UI доступен;
  - что watch page доступна;
  - что accessibility tree читается;
  - что базовые player элементы вообще видны.

Это не production-код, а техническая разведка.

Его цель:

- снять риск неправильного выбора инструментов;
- заранее понять, насколько стабильны selectors и player surface.

## 23. Что не входит в первый этап

- iOS runtime
- full feature parity with browser
- complete ad capture parity
- production-grade dockerized emulator farm
- перенос AdsPower browser profile в app

## 23.1. Эмпирические выводы первого live spike

Первый живой spike уже подтвердил несколько важных operational фактов:

- обычный probe-runtime изначально был стабильнее в headless-режиме:
  - `-no-window`
  - `-no-audio`
- bootstrap flow для warm snapshot должен идти в видимом AVD; на этом Mac это удалось
  подтвердить отдельно после перехода на dedicated Play Store image.
- для детерминированного старта нужно отключить quickboot snapshots:
  - `-no-snapshot-load`
  - `-no-snapshot-save`
- `ANDROID_SDK_ROOT` должен содержать `platform-tools` как поддиректорию;
  Homebrew-установка, где `adb` лежит отдельно в cask path, требует symlink или иной
  явной нормализации SDK layout.
- `Appium + UiAutomator2` session на этом окружении поднимается корректно после
  стабилизации AVD boot path.
- нативный YouTube app на текущем system image открывается, но сразу показывает
  mandatory upgrade screen:
  `com.google.android.apps.youtube.app.application.upgrade.NewVersionAvailableActivity`
- `google_apis_playstore` image на macOS может создавать `AVD`, где в `config.ini`
  одновременно лежат:
  - `PlayStore.enabled=no`
  - `tag.id=google_apis_playstore`
  Поэтому authoritative признаком Play Store image должен считаться не только
  `PlayStore.enabled`, но и tag/image metadata.

Практический вывод:

- `warm snapshot lifecycle` — это не вторичный operational detail, а обязательная
  ранняя часть Android path;
- первый usable snapshot должен содержать уже обновленный YouTube app, иначе runtime
  будет упираться в startup gate раньше поиска и просмотра.

## 24. Рекомендуемый порядок внедрения

### Phase A. Foundation

- Milestone 0
- Milestone 1
- Milestone 2
- Milestone 3

### Phase B. Working MVP

- Milestone 4
- Milestone 5
- Milestone 6

### Phase C. Behavioral parity

- Milestone 7

### Phase D. Ads

- Milestone 8
- Milestone 9

### Phase E. Hardening

- Milestone 10

## 25. Финальная рекомендация

Правильная цель не "перетащить AdsPower profile в app".

Правильная цель:

- оставить существующий browser runtime как рабочий baseline;
- использовать AdsPower profile как **template source**;
- добавить рядом **Android native runtime**;
- развивать его как отдельную execution branch с общими domain/persistence слоями.

Это единственный путь, который:

- не ломает текущую систему;
- дает читабельную архитектуру;
- масштабируется;
- и не превращает текущий Playwright runtime в смесь несовместимых абстракций.
