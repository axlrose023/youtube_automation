# Android Session Efficiency Optimization Plan

Этот файл является каноническим планом по оптимизации эффективности Android
native YouTube runtime.

Цель документа:

- зафиксировать текущее понимание bottleneck-ов;
- не обсуждать стратегию заново в следующих сессиях;
- иметь один source of truth перед будущей реализацией;
- оптимизировать runtime без деградации рекламного пайплайна и без ломки
  текущего поведения фронта и backend contracts.

Этот документ не является реализацией. Он описывает только план, ограничения,
приоритеты, критерии и порядок работ.

Он опирается на:

- [android-native-youtube-runtime-plan.md](./android-native-youtube-runtime-plan.md)
- текущий runtime в
  [backend/src/app/services/mobile_app/android/runner.py](../backend/src/app/services/mobile_app/android/runner.py)
- навигацию в
  [backend/src/app/services/mobile_app/android/youtube/navigator.py](../backend/src/app/services/mobile_app/android/youtube/navigator.py)

## 1. Контекст

### 1.1. Что именно оптимизируем

Оптимизируется не "скорость кода" в абстракции, а эффективность 15-минутной
Android-сессии.

Практический критерий:

- за фиксированное wall-clock окно нужно получать больше чистого
  медиапотребления;
- при этом нельзя терять критичные ad artifacts;
- нельзя ухудшать устойчивость Android runtime;
- нельзя ломать текущую модель хранения истории, capture-ов и frontend detail
  view.

### 1.2. Что считается чистым медиапотреблением

Для этого плана под чистым медиапотреблением считаем:

- фактические секунды просмотра обычного видео;
- фактические секунды сохраненного ad video.

Не считаем медиапотреблением:

- поиск;
- возвраты назад;
- reset в home;
- повторные поисковые циклы;
- ожидания timeouts;
- служебные CTA/landing переходы;
- системное восстановление поверхности YouTube.

### 1.3. Подтвержденный локальный baseline

Опорный прогон:

- session id: `72e3f191-238a-4034-be0d-f60363cf5275`
- duration: `15 min`
- session runtime: `891s`
- watched videos: `4`
- watched ads: `5`
- watched video seconds: `311.0s`
- recorded ad seconds: `171.2s`
- estimated pure media consumption: `482.2s`

Следствие:

- около `408.8s` ушло в operational overhead;
- это уже лучше провальных прогонов уровня `~180s`, но всё еще оставляет
  большой запас для оптимизации.

## 2. Зафиксированные наблюдения

### 2.1. Наблюдения по логам и состоянию runtime

В текущем коде и логе подтверждены следующие точки:

- `wait_for_results` является одним из главных bottleneck-ов bootstrap path и
  в ref-сценарии склонен уходить в timeout/recovery, а не в быстрый успех;
- `open_first_result` является вторым крупным bottleneck-ом bootstrap path:
  явного timeout может не быть, но сам hot path остается слишком долгим;
- консервативная остановка перед новым topic cycle:
  `stop:no_time_for_next_topic` в
  [runner.py](../backend/src/app/services/mobile_app/android/runner.py)
  около `_next_topic_start_buffer_seconds`;
- повторяющийся `topic_post_reset timed out` между topic runs; при этом текущий
  код уже делает timeout -> launcher recovery, то есть проблема сначала выглядит
  как вопрос калибровки deadline, а не отсутствия tiered recovery;
- main watch нередко продолжается через `_continue_main_watch_if_needed`, но
  затем происходит дорогой переход к следующей теме;
- midroll continuation уже частично схлопывается через
  `_midroll_continues_previous_ad` и
  `_merge_midroll_continuation_into_previous_ad`, но ad path все еще тяжелый;
- длинные ads все еще записываются неполно;
- часть ad metadata смешивается с чужим landing.

### 2.2. Подтвержденные симптомы, которые нельзя забыть

#### A. Потери wall-clock на post-topic reset

В текущем runtime после topic run вызывается:

- `_reset_to_home_with_timeout(...)`
- `stage_label="topic_post_reset"`

Судя по логу, этот этап несколько раз заканчивается timeout-ом, но сессия потом
восстанавливается и идет дальше.

Это плохой сценарий, потому что:

- тратится wall-clock;
- нет пользы для медиапотребления;
- tail 15-minute session съедается именно operational overhead.

#### B. Слишком дорогой многотемный режим для короткой сессии

В текущей логике `_next_topic_start_buffer_seconds(...)`,
`_decide_session_target_watch_seconds(...)` и `_should_retry_topic_attempt(...)`
сессия старается сохранить покрытие тем.

Это полезно для coverage, но для короткой сессии создает высокий overhead:

- repeated search/open cycles;
- repeated reset to home;
- повторный разгон контекста рекомендаций;
- раннее завершение tail из-за topic buffer.

#### C. Ad capture остается в слишком горячем пути

В текущем поведении Android runtime должен:

- зафиксировать ad surface;
- открыть CTA;
- получить landing screenshot;
- сохранить ad video;
- передать в фоновый scrape/analysis pipeline.

Это правильный продуктовый поток, но на уровне performance он все еще слишком
толстый, особенно для длинных ads и частых ad interruptions.

#### D. Смешивание ad identity и landing identity

В одном локальном прогоне подтверждены кейсы, где:

- ad metadata в capture относилась к одному рекламодателю;
- `landing_url` и сохраненный landing dump относились к другому.

Это значит, что optimization work нельзя вести в отрыве от ad identity guards.
Нельзя ускорять поток ценой еще более частого смешивания реклам.

#### E. Длинные ads все еще режутся

Зафиксированы случаи:

- `92s` ad -> `~59s` video
- `73s` ad -> `~55s` video

То есть часть прогресса уже есть, но логика stop/wait/focus все еще режет
длинный ad tail.

## 3. Ограничения и инварианты

### 3.1. Что нельзя ломать

В ходе будущей реализации нельзя ломать:

- Android native runtime как отдельную ветку runtime;
- frontend contracts detail/history/dashboard;
- persistence модели `emulation_sessions` и `ad_captures`;
- background landing scrape pipeline;
- background ad analysis pipeline;
- существующий flow, где landing screenshot делает Android/emulator, а
  Playwright уже в фоне сохраняет landing полноценно;
- возможность ручного просмотра ad video и landing artifacts.

### 3.2. Что не делаем ради performance

Нельзя ради процентов эффективности:

- полностью отключить landing open;
- отказаться от ad video capture;
- выкинуть ad screenshots;
- убрать persisted ad captures;
- случайно превратить runtime в "просто долго смотрим одно видео";
- ломать текущую recoverability на нестабильных surfaces.

### 3.3. Допустимые компромиссы

Допускаются только такие компромиссы:

- меньше тем за 15 минут;
- меньше полных reset-циклов;
- более агрессивный reuse текущей выдачи;
- быстрый endgame вместо нового search cycle;
- более ранний exit из неполезного topic path;
- более строгий fast-path/fallback path вместо timeout-based recovery.

## 4. Целевые метрики

### 4.1. Основная цель

Поднять долю чистого медиапотребления в 15-минутной Android-сессии.

### 4.2. Целевой диапазон

Практический target для balanced режима:

- minimum acceptable: `560s+`
- good: `600-650s`, но только после исправления bootstrap path и перехода на
  2-topic policy для 15-минутной сессии
- strong: `650-700s` только как stretch target при еще более низком search
  overhead и меньшем topic diversity

Выше `700s` возможно, но обычно уже требует жертвовать:

- разнообразием тем;
- полнотой ad flow;
- степенью realism текущего поведения.

### 4.3. Обязательные guard metrics

Одновременно нужно держать под контролем:

- average wall-clock per topic transition;
- count of `topic_post_reset` failures;
- average search/open overhead;
- ad capture count;
- fraction of ads with valid probe screenshots;
- fraction of ads with valid landing screenshot;
- fraction of ads with non-mixed identity;
- fraction of long ads with recorded duration >= `80%` of effective ad window;
- fraction of sessions that still finish in `completed`, а не в `failed`.

## 5. Проблема в виде hot path

### 5.1. Текущий упрощенный hot path

Текущий session path выглядит так:

1. acquire emulator/session
2. reset/ensure app ready
3. open search
4. submit search
5. wait results
6. open first result
7. watch + sample + verify
8. detect ad
9. record ad
10. CTA flow
11. landing screenshot
12. return to watch
13. optional extra watch
14. topic done
15. reset to home
16. repeat for next topic

Самые дорогие и подозрительные шаги:

- `3-6`
- `10-15`

### 5.2. Где фактически теряется время

В первую очередь время теряется не в самом просмотре, а в:

- `wait_for_results` bootstrap bug/timeout path;
- `open_first_result` slow path даже без timeout;
- `topic_post_reset` timeout + recovery path;
- repeated topic bootstrap;
- reset-to-home timeouts;
- search/open overhead;
- cautious next-topic buffer;
- ad flow, который слишком долго остается в blocking path;
- повторном разогреве контекста после reset.

В ref-сценарии именно эти три точки съели самый дорогой кусок overhead:

- `wait_for_results`: около `84s`
- `open_first_result`: около `93s`
- `topic_post_reset`: около `60-80s`

Именно поэтому bootstrap correctness и timeout calibration должны идти раньше,
чем cluster reuse.

## 6. Главная стратегия оптимизации

### 6.1. Основной принцип

Нужно не "ускорить каждый шаг понемногу", а:

- вынести тяжелое из горячего пути;
- сократить число hot-path шагов;
- заменить timeout-based recovery на deterministic short fallback;
- уменьшить количество полных topic transitions;
- добирать просмотр внутри уже открытого кластера, а не через новый search.

### 6.2. Базовый behavioral shift

Целевой shift для 15-minute sessions:

- не `many topic cycles`
- а `one bootstrap -> several efficient watches in one cluster -> fast tail finish`

Это не означает отказ от topic coverage совсем. Это означает, что coverage
больше не должна быть главной ценой для короткой сессии.

Практическое правило для 15-minute sessions:

- default policy должна исходить из `2 topics max`;
- третья тема допустима только при явном избытке remaining time и после того,
  как исчерпан текущий кластер.

## 7. Целевая модель поведения

### 7.1. Topic cluster first

Вместо независимого обращения к каждой теме с полным reset cycle вводится
понятие topic cluster.

Пример:

- `quantum ai trading`
- `forex trading strategy`
- `ai trading bot`

Это для рантайма не три независимых мира, а один тематический кластер.

Целевое поведение:

- первый search задает кластер;
- дальше runtime должен максимально жить внутри релевантной выдачи,
  recommendations и related list;
- повторный search делать только при потере релевантности или recovery case.

### 7.2. One bootstrap search, not one search per topic

Для коротких сессий целевой path:

1. стартовый search по strongest anchor topic;
2. выбор хорошего первого результата;
3. watch loop;
4. follow-up выбор следующего кандидата из текущего surface;
5. только при провале fallback в search.

### 7.3. Fast endgame

Если до конца сессии осталось мало времени:

- нельзя запускать новый полный topic cycle;
- нельзя жечь время на `reset -> search -> open`.

Нужно:

- добирать остаток на текущем экране;
- либо быстро открыть следующий видимый кандидат из текущего feed/results;
- либо завершать сессию без дорогого "последнего рывка".

## 8. Фазовый план

### 8.1. Phase 0. Use existing stage timings and add a session summary

### Цель

Не изобретать вторую систему измерений, если первая уже частично есть.

### Что уже есть

В текущем рантайме уже пишутся stage-level заметки вида:

- `submit_search_seconds`
- `wait_for_results_seconds`
- `open_first_result_seconds`
- `topic_post_reset_seconds`
- timeout/fallback notes для этих стадий

То есть проблема не в полном отсутствии измерений, а в том, что нет удобного
session-level агрегатора.

### Что реально нужно добавить

- session summary поверх существующих timings;
- агрегаты:
  - `pure_media_seconds`
  - `video_watch_seconds`
  - `recorded_ad_seconds`
  - `bootstrap_overhead_seconds`
  - `bootstrap_overhead_per_topic`
  - `post_reset_overhead_seconds`
  - `ad_flow_overhead_seconds`
- counters:
  - `topic_reset_attempts`
  - `topic_reset_timeouts`
  - `wait_for_results_timeouts`
  - `mixed_identity_detected_count`
  - `search_cycles`
  - `topics_started`
  - `topics_completed`
  - `same_cluster_reuses`
  - `endgame_abort_new_topic`

### Почему это первая фаза

Потому что это cheap win и хороший foundation, но не отдельная большая
архитектурная эпопея.

### Acceptance criteria

- ref-сессия и следующие прогоны дают session summary без ручного разборa notes;
- можно сравнить run-to-run итоги по чистому медиа и bootstrap overhead.

### 8.2. Phase 1. Cheap wins: timeout calibration and search bootstrap correctness

### Цель

Убрать самые дешевые и самые явные потери времени до любых более сложных
изменений поведения.

### Почему это должно идти первым

Cluster reuse и endgame optimization полезны только если search bootstrap уже
не сломан.

Пока:

- `wait_for_results` системно дорогой;
- `open_first_result` системно дорогой;
- `topic_post_reset` часто попадает в timeout path.

Значит сначала нужно починить bootstrap correctness и timeout calibration.

### 8.2.1. Подфаза A. Reset timeout calibration

#### Наблюдение

Сейчас `_reset_to_home_with_timeout(...)` уже имеет tiered behavior:

- `navigator.reset_to_home(deadline=...)`
- timeout path
- `_recover_from_launcher_anr_with_timeout(...)`
- raise

То есть архитектурный tiered recovery уже есть.

#### Вывод

Первая проблема здесь не "отсутствие tiered recovery", а слишком маленький
default timeout `15.0s` для текущего AVD/сценария.

#### План

- сначала измерить p95 для successful reset path на session summary из Phase 0;
- только после этого откалибровать timeout как `p95 + safety buffer`;
- practical starting corridor для первой проверки: `22-25s`, но это не
  каноническое число, а лишь вероятный initial probe;
- только если это не решает проблему, уже трогать deeper recovery design.

#### Кодовые точки

- `_reset_to_home_with_timeout(...)`
- `navigator.reset_to_home(...)`
- `_reset_to_home_sync(...)`

#### Acceptance criteria

- `topic_post_reset_timeout` резко падает или исчезает в ref-сценарии;
- launcher recovery перестает быть нормальным happy path.

### 8.2.2. Подфаза B. Search bootstrap correctness

#### Наблюдение

`wait_for_results` в ref-сценарии выглядит не как "просто медленно", а как
системный correctness bug в predicate/results detection.

#### План

Нужно починить именно readiness detection:

- early-return по первому видимому openable result;
- более слабый/устойчивый predicate готовности;
- не полагаться только на слишком строгую комбинацию results container + query
  surface;
- сохранить fallback path, но перестать делать timeout нормальным путем.

#### Кодовые точки

- `navigator.py:_wait_for_results_sync(...)`
- `navigator.py:_has_query_ready_surface_sync(...)`
- `navigator.py:_has_query_results_surface_sync(...)`
- `runner.py:_wait_for_results_with_timeout(...)`

#### Acceptance criteria

- `wait_for_results` перестает быть стадией с near-100% timeout/recovery rate;
- среднее время `wait_for_results` существенно падает.
- `no_result_opened` не растет относительно baseline ref-сценария.

### 8.2.3. Подфаза C. Open-first-result hot path audit

#### Наблюдение

`open_first_result` уже сейчас дорогой даже без явного timeout.

Внутри видно:

- repeated result surface recovery;
- repeated dialog handling;
- conservative sleep blocks;
- много попыток tap/open loops.

#### План

Нужно отдельно измерить и сократить:

- pre-tap settle waits;
- unnecessary `recover_results_surface` calls, если results already ready;
- dwell/scroll/tap loops, которые не добавляют ценности;
- sponsor-skip path, если ready organic result уже доступен.

#### Кодовые точки

- `navigator.py:_open_first_result_sync(...)`
- `runner.py:_open_first_result_with_timeout(...)`

#### Acceptance criteria

- `open_first_result` средне и p75 заметно сокращаются;
- падение не достигается ценой роста `no_result_opened`.

### 8.2.4. Суммарный критерий для Phase 1

Для ref-сценария Phase 1 успешна, если:

- `wait_for_results` больше не ведет себя как системный timeout path;
- `topic_post_reset` больше не timeout-ится как норма;
- `bootstrap_overhead_per_topic` падает до рабочего коридора;
- появляются первые `+60-100s` чистого медиапотребления без смены ad strategy.

### 8.3. Phase 2. Session-duration-aware topic budget

### Цель

Перестать пытаться покрывать слишком много тем в короткой сессии.

### Базовое правило

Для короткой Android-сессии нужен hard gate по количеству тем.

Рекомендуемая формула:

- `max_topics = max(1, floor((duration_minutes - 2) / 5))`

Практический вывод:

- `15 min -> 2 topics max`

Третью тему можно запускать только если:

- после двух тем остался явный запас времени;
- текущий кластер исчерпан;
- expected benefit лучше, чем добор текущего watch/endgame path.

### Почему это раньше cluster reuse

Потому что это простой, дешевый и очень эффективный guardrail.

Важно:

- hard topic cap и endgame gate не конкурируют;
- они работают вместе;
- hard cap ограничивает верхний предел числа тем;
- endgame gate запрещает дорогой новый bootstrap, если remaining time уже
  слишком мал или текущий watch path еще выгоднее;
- это особенно важно для ad-heavy sessions, где даже `2 topics` могут быть
  верхней границей, а не обязательным минимумом.

### Кодовые точки

- gate перед новым search/topic cycle;
- `_next_topic_start_buffer_seconds(...)`
- `_decide_session_target_watch_seconds(...)`
- возможный новый helper для session topic budget

### Acceptance criteria

- 15-минутные сессии по умолчанию не раздуваются до 4 тем;
- исчезают хвосты вида "еще одна тема ради 20-30s просмотра".

### 8.4. Phase 3. Cluster reuse after bootstrap correctness

### Цель

Сократить количество полных поисковых циклов без потери релевантности, но уже
после того, как сам bootstrap path стал рабочим.

### Идея

После первого удачного входа в тему/кластер рантайм должен:

- использовать current results/recommendations/related context;
- не делать новый full search по каждому topic;
- считать re-search recovery tool, а не default path.

### Что меняется в поведении

- `resolved_topics` остаются input-level сущностью;
- на runtime-level появляется `cluster_context`;
- first topic дает начальный anchor;
- subsequent picks идут из текущего surface;
- возврат к search только если качество текущего candidate pool упало ниже
  порога.

### Кодовые точки

- `AndroidYouTubeNavigator`
- result selection logic
- watch loop after first opened title
- `_covered_topics(...)`
- `_decide_session_target_watch_seconds(...)`

### Acceptance criteria

- search cycles per 15-min session заметно падают;
- чистое медиапотребление растет без деградации ad capture count;
- sessions с `1-2` search cycles становятся нормой для короткого прогона.

### 8.5. Phase 4. Aggressive endgame optimization

### Цель

Перестать терять последние `30-120s` сессии на новый search/bootstrap.

### Целевая стратегия

Добавить отдельный endgame mode:

- если `remaining_seconds < 120`, новый search запрещен;
- если есть текущий watch surface, добираем там;
- если есть cheap next candidate из current cluster, берем его;
- если нет, завершаем сессию аккуратно.

### Что нельзя делать

Нельзя превращать endgame в хаотичный серфинг или в hidden third-topic path.

### Acceptance criteria

- tail session не заканчивается пустым operational gap;
- endgame повышает полезный media time без всплеска нерелевантных открытий.

### 8.6. Phase 5. Fix ad identity coherence before deeper ad acceleration

### Цель

Устранить или резко сократить случаи, где:

- creative metadata относится к одной рекламе;
- landing dump относится к другой.

### Почему это раньше ad hot path thinning

Потому что ускорять ad path без identity guard опасно: можно просто быстрее
производить неправильные captures.

### Подход

Нужно ввести explicit ad identity guard:

- capture-level identity key;
- CTA/open/landing должны быть привязаны к одному ad capture envelope;
- любое сомнение должно помечать capture как ambiguous, а не silently merge.

### Возможные ключи

- aclk `ai` param;
- normalized redirect target;
- ad duration + progress continuity;
- display_url host/path;
- landing final host;
- screen timestamp correlation;
- capture generation id.

### Кодовые точки

- `_dedupe_watched_ads(...)`
- `_midroll_continues_previous_ad(...)`
- `_merge_midroll_continuation_into_previous_ad(...)`
- `_backfill_advertiser_from_landing_scrape(...)`
- persistence/ad capture merge path

### Acceptance criteria

- mixed identity cases становятся rare and explicit;
- ad records либо coherent, либо marked ambiguous;
- analysis работает по coherent captures.

### 8.7. Phase 6. Finish long-ad recorder correctness

### Цель

Добиться того, чтобы long ads перестали системно резаться.

### Что видно сейчас

Логика wait-before-stop уже есть, но:

- effective remaining ad window рассчитывается не всегда точно;
- recorder stop все еще может происходить слишком рано;
- focus window потом уже не спасает недозаписанный хвост.

### Подход

Нужно разделить:

- ad timing estimation;
- recorder lifetime;
- focused trimming/post-processing.

Запись должна жить по более надежному критерию, чем текущая смесь:

- samples progress
- debug xml progress
- elapsed since samples
- fixed cap

### Acceptance criteria

- длинные ads стабильно дают video длительностью, близкой к реальному ad span;
- исчезают кейсы, где `70-90s` ad превращается в `~55-60s` без уважительной
  причины.

### 8.8. Phase 7. Thin the ad hot path without losing artifacts

### Цель

Сделать ad pipeline дешевле для горячего пути, но только после того, как
identity и recorder correctness уже приведены в порядок.

### Что должно остаться

Обязательно сохранить:

- ad video
- probe screenshots
- Android landing screenshot
- landing URL / CTA URL
- background Playwright landing scrape
- background ad analysis

### Что надо изменить концептуально

Горячий путь должен делать только minimum required capture:

1. сохранить ad UI state;
2. записать ad video;
3. открыть CTA;
4. дождаться enough-for-screenshot состояния;
5. снять landing screenshot;
6. сохранить final URL;
7. быстро вернуться в YouTube.

Все остальное:

- heavy landing scrape;
- asset harvesting;
- relevance analysis;
- secondary enrichments;

должно быть гарантированно вне blocking path.

### Acceptance criteria

- ad flow становится короче по wall-clock;
- при этом сохраняются все требуемые capture artifacts;
- не растет доля broken/mixed ad records.

### 8.9. Phase 8. Adaptive watch verification and sampling

### Цель

Уменьшить служебный overhead watch verification без потери устойчивости.

### Идея

Разные стадии просмотра не требуют одинаковой частоты sampling.

Пример целевой логики:

- aggressive sampling в первые секунды после открытия;
- standard sampling во время стабильного organic watch;
- fast re-sampling только после surface disturbance;
- отдельный режим sampling во время ads.

### Что важно

Эта фаза должна идти последней. Сначала нужно исправить gross overhead:

- bootstrap bugs
- topic budget
- topic churn
- ad identity / recorder correctness

### Acceptance criteria

- sampling становится дешевле;
- regression по watch verification не появляется.

## 9. Приоритеты по ожидаемому выигрышу

Если расставить задачи по ожидаемому practical impact и дешевизне победы:

1. reset timeout calibration
2. `wait_for_results` correctness
3. `open_first_result` hot path trim
4. session-duration-aware topic budget
5. cluster reuse
6. endgame mode
7. ad identity coherence
8. long-ad recorder correctness
9. thin ad hot path
10. adaptive sampling

Причина такого порядка:

- первые 4 пункта бьют в подтвержденный bootstrap waste;
- cluster reuse и endgame дают следующий прирост только после рабочего
  bootstrap;
- ad acceleration переносится ниже, потому что сначала надо защитить
  correctness ad captures.

## 10. Что не надо делать в первой итерации

Чтобы не сломать текущую систему, в первой реализации нельзя:

- переписывать весь `AndroidYouTubeSessionRunner` с нуля;
- использовать cluster reuse как способ замаскировать сломанный bootstrap;
- оставлять 15-minute sessions без hard cap по темам;
- сливать все cheap wins Iteration A в один большой feature flag;
- объединять Android и browser runtime;
- менять frontend schema contracts;
- перестраивать persistence модель;
- превращать все в новый "режим" через десятки feature flags сразу;
- одновременно чинить performance, relevance prompt, deploy и UI.

Первая итерация должна быть узкой:

- session summary aggregator поверх существующих timings;
- hard cap по темам для коротких сессий.
- reset timeout calibration;
- fix `wait_for_results`;
- trim `open_first_result`.

Дополнительное правило:

- каждая подфаза Iteration A должна идти отдельным commit-ом;
- откат должен быть возможен через точечный `git revert`;
- не должно быть одного общего флага вида `USE_NEW_BOOTSTRAP_PATH`, который
  смешивает несколько гипотез в один rollout.

## 11. План валидации

### 11.1. Базовый режим тестирования

Каждый этап должен проверяться на серии одинаковых прогонов:

- duration: `15 min`
- same proxy setup
- same AVD/account family
- same topic cluster
- same logging level

### 11.2. Минимальный набор сценариев для итеративной работы

Для повседневной итерации достаточно:

1. ref-сценария `quantum ai trading / forex trading strategy / ai trading bot`
2. одного edge-case сценария

Расширенная матрица из 5 сценариев нужна уже для финального приемочного этапа.

### 11.3. Финальный приемочный набор сценариев

1. `quantum ai trading / forex trading strategy / ai trading bot`
2. 1-topic session
3. 2-topic clustered session
4. ad-heavy session
5. session without ads

### 11.4. Что сравнивать

До/после сравнивать:

- pure media consumption
- wall-clock overhead by stage
- `bootstrap_overhead_seconds`
- `bootstrap_overhead_per_topic`
- `wait_for_results` timeout rate
- `open_first_result` p75
- topic reset count and timeout count
- watched videos count
- watched ads count
- ad artifact completeness
- mixed identity incidents
- long-ad recorder completeness

### 11.5. Критерий успешности итерации

Каждая фаза считается успешной только если:

- efficiency выросла;
- session completion stability не упала;
- ad artifacts не деградировали;
- новые ambiguous cases не выросли.

### 11.6. Regression guard

Нужен scripted regression gate для reference runs.

Минимальное требование:

- на каждой итерации ref-сценарий не должен опускаться ниже
  `pure_media >= baseline * 0.95`;
- capture artifacts должны оставаться полными;
- если полный Android CI слишком тяжел, этот gate должен существовать хотя бы
  как обязательный scripted acceptance run на выделенной машине.

## 12. Предлагаемый порядок будущей реализации

### Iteration A. Cheap wins

- сначала добавить session summary aggregator поверх существующих timings;
- затем ввести hard cap по темам:
  `max_topics = max(1, floor((duration_minutes - 2) / 5))`;
- затем поднять `_reset_to_home_with_timeout` default timeout до empirically
  measured `p95 + safety buffer`;
- починить `wait_for_results` через early-return и ослабление readiness
  predicate;
- уменьшить overhead в `open_first_result`;

Ожидаемый эффект:

- `+60-100s` pure media при минимуме behavioral changes.

Развёрнутая карта ожиданий по источникам эффекта:

- session summary aggregator:
  - сам по себе не даёт прироста;
  - нужен как measurement foundation;
- hard topic cap:
  - напрямую не "ускоряет" bootstrap;
  - но не даёт тратить tail сессии на лишний третий/четвертый topic cycle;
- reset timeout calibration:
  - ожидаемая экономия порядка `~40-60s` за `15 min`, если timeout/recovery
    path действительно был нормой;
- `wait_for_results` fix:
  - ожидаемая экономия порядка `~60-80s` за `15 min` в ref-сценарии;
- `open_first_result` trim:
  - ожидаемая экономия порядка `~20-40s` за `15 min`;
- hard topic cap + end-of-session bootstrap avoidance:
  - могут дополнительно освободить до `~60-90s` wall-clock для полезного watch
    path.

Важно:

- эти оценки являются planning ranges, а не обещанием;
- они не полностью аддитивны;
- но именно они объясняют, почему target `650-700s` выглядит достижимым после
  успешной Iteration A и последующих cluster/endgame изменений.

Порядок внутри Iteration A является обязательным:

1. session summary aggregator
2. hard topic cap
3. reset timeout calibration
4. `wait_for_results` fix
5. `open_first_result` trim

### Iteration B. Cluster + endgame

- endgame mode:
  `remaining < 120s -> no new search`
- cluster reuse:
  после первого opened video брать следующий кандидат из current cluster без
  возврата в search, если это возможно

### Iteration C. Ad correctness

- ad identity guard
- long-ad recorder correctness

### Iteration D. Polish

- thin ad hot path
- adaptive sampling

### Почему именно так

Сначала нужно:

- убрать подтвержденные bugs и cheap waste;
- потом менять поведение выбора следующего видео;
- только затем ускорять ad path.

Если Iteration A дает меньше `~30s` прироста чистого медиа на ref-сценарии:

- нельзя автоматически переходить в Iteration B;
- нужно пересмотреть bootstrap гипотезы;
- нужно заново разложить session summary и проверить, где фактический overhead
  все еще доминирует;
- только после этого решать, нужен ли deeper navigator work или rollback
  отдельных cheap-win изменений.

## 13. Практические решения, которые сейчас выглядят адекватными

На текущем этапе адекватными выглядят такие будущие изменения:

- timeout calibration вместо немедленной перестройки recovery architecture;
- fix `wait_for_results` до cluster reuse;
- audit `open_first_result` как отдельного bottleneck-а;
- жесткий `2 topics max` policy для `15 min` по умолчанию;
- cluster-first логика только поверх рабочего bootstrap;
- endgame mode без нового topic bootstrap;
- ad identity guard до любых ускорений ad merge path;
- session summary metrics поверх уже существующих stage timings.

Неадекватными сейчас выглядят такие идеи:

- просто убрать landing flow;
- просто отключить ad recording;
- просто смотреть одно длинное видео всю сессию;
- пытаться лечить `wait_for_results` только cluster reuse-ом;
- сразу переписать runtime целиком.

## 14. Definition of Done для всего roadmap

Roadmap можно считать выполненным, когда одновременно выполняются условия:

- 15-minute ref-сценарий стабильно дает `600s+` чистого медиапотребления;
- `15 min` режим по умолчанию не раздувается выше `2 topics`, если нет явного
  override;
- `bootstrap_overhead_per_topic < 35s` на `p75`;
- `wait_for_results` перестает быть системным timeout path;
- `topic_post_reset timeout` больше не является частой нормой;
- число full search cycles за короткую сессию заметно снижается;
- endgame перестает терять полезный tail;
- long ads записываются существенно полнее;
- mixed ad identity случаи либо исчезают, либо явно маркируются;
- scripted regression gate не пропускает деградацию ниже baseline;
- frontend продолжает видеть все текущие capture artifacts без contract regressions.

## 15. Статус документа

Текущее состояние:

- план пересобран с учетом ref-сценария и подтвержденных bottleneck-ов;
- реализация не начата;
- документ должен использоваться как reference перед любой следующей
  оптимизацией Android runtime efficiency.
