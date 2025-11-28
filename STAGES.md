**Переформулировка / Restated**

* **RU.** Тебе нужен поэтапный план разработки, где на каждом этапе получается *железный, осязаемый deliverable* — набор базовых классов/типов/интерфейсов/методов с чётким функционалом и definition of done. После завершения этапа он считается «базисом», в который дальше не лезем, а только наращиваем поверх.

Ниже — такая декомпозиция. Я разбил её на 9 уровней, от минимального ядра до полного компилятора с DSL.

---

## Этап 0. Мини‑ядро исполнения графа (Raw‑режим)

**Цель:** иметь работающий граф из нод с простым, детерминированным порядком исполнения и без всяких DSL/анализов. Это «микроядро».

### API и сущности

1. `NodeId`, `PortId`

   * Простые идентификаторы нод и портов (строка/инт, но типы отдельные).
   * Гарантия: уникальность внутри графа.

2. `class RawNode`

   * Абстрактный базовый класс для нод «низкого уровня».
   * Минимальный протокол:

     ```python
     class Context:
         def read(self, port_or_state): ...
         def write(self, port_or_state, value): ...

     class RawNode(ABC):
         id: NodeId
         inputs: dict[str, Port]
         outputs: dict[str, Port]

         @abstractmethod
         def step(self, ctx: Context) -> None:
             ...
     ```

3. `class GraphRuntime`

   * Хранит:

     * множество нод (`RawNode`),
     * список directed edges `Port → Port`,
     * порядок исполнения (`List[NodeId]`), полученный топосортом.
   * Методы:

     ```python
     def add_node(self, node: RawNode) -> None: ...
     def connect(self, src: Port, dst: Port) -> None: ...
     def build_schedule(self) -> None:  # топосорт
     def run_step(self) -> None:        # один тик
     ```

4. `RuntimeState`

   * Простое хранилище значений портов между тиками: `Map[PortId, Any]`.

### Семантика

* Граф — строго **acyclic** (компилятор/рантайм делает топосорт и ругается при цикле).
* Каждый `run_step`:

  * идёт по `schedule: List[NodeId]`;
  * для каждой ноды вызывает `node.step(ctx)`;
  * `ctx.read` возвращает «последнее записанное значение в прошлом тике» или `None`;
  * `ctx.write` обновляет значение в `RuntimeState`.

### Definition of Done (Этап 0)

* Есть минимальная библиотека:

  * `RawNode`, `Context`, `GraphRuntime`, `Port`.
* Есть юнит‑тесты:

  * простой линейный граф `A→B→C` — значения протекают корректно;
  * DAG с двумя ветками, которые сходятся — порядок даёт ожидаемый результат;
  * попытка создать цикл → явная `CycleError`.
* Явно задокументирована семантика: один тик, один schedule, без microsteps, без DSL.

> Всё, что дальше — **не ломает** этих классов и не меняет базовую семантику `RawNode.step(ctx)` и `GraphRuntime`.

---

## Этап 1. Модель переменных и Intents, фазы propose/resolve/commit

**Цель:** избавиться от «прямых» сайд‑эффектов в состояние мира, ввести **Variables** и трёхфазное обновление. Этот слой — основа для детерминизма и merge‑политик.

### API и сущности

1. `class Variable[T]`

   * Атрибуты:

     ```python
     name: str
     init: T
     write_policy: WritePolicy[T]
     ```
   * Абстракция «глобальной переменной мира», не привязанной к конкретной ноде.

2. `class WritePolicy[T]`

   * Интерфейс:

     ```python
     class WritePolicy(ABC, Generic[T]):
         @abstractmethod
         def merge(self, values: list[T]) -> T: ...
     ```
   * Стандартные реализации:

     * `Error()` — не допускает >1 значения;
     * `Sum()`, `Max()`, `Min()`;
     * `LWW(order: List[NodeId])`.

3. `class Intent`

   * Внутренний тип:

     ```python
     @dataclass
     class Intent(Generic[T]):
         variable: Variable[T]
         producer: NodeId
         value: T
     ```

4. `class IntentContext(Context)`

   * Расширяет контекст:

     ```python
     class IntentContext(Context):
         def read_var(self, var: Variable[T]) -> T: ...
         def write_var(self, var: Variable[T], value: T) -> None: ...
     ```

5. Расширенный `GraphRuntime`:

   * Добавляет три фазы:

     ```python
     def run_tick(self) -> None:
         self._propose_phase()
         self._resolve_phase()
         self._commit_phase()
     ```

   * `_propose_phase` — обходит ноды (по schedule), даёт им `IntentContext`; они складывают intents в список.

   * `_resolve_phase` — группирует intents по `Variable`, прогоняет `write_policy.merge`.

   * `_commit_phase` — обновляет значения `Variable` и портов (чтобы значения были доступны в следующем тике).

### Семантика

* Нодам **запрещено** напрямую мутировать `Variable` — только через intents.
* В рамках одного тика для каждой `Variable` создаётся **ровно одно** итоговое значение (либо ошибка для `Error()`).
* Порядок вызова нод в фазе propose **не должен** влиять на итоговое значение переменной, если `write_policy` коммутативен/ассоциативен или есть determinist LWW.

### Definition of Done (Этап 1)

* Реализованы:

  * `Variable`, `WritePolicy`, `Intent`, `IntentContext`.
  * Трёхфазное исполнение `run_tick`.
* Тесты:

  * Две ноды пишут дельты в `delta_cash`, `write_policy=Sum()`: итог `cash` не зависит от порядка.
  * Две ноды пишут в переменную с `Error()` → ошибка в `_resolve_phase`.
  * LWW‑политика с заданным порядком выдаёт одинаковый результат при перестановке schedule.

> После этого этапа мы **не трогаем** модель `Variable`, `WritePolicy` и фазовую семантику; всё остальное строится поверх.

---

## Этап 2. IR и компиляционная труба (ещё без DSL)

**Цель:** отделить Python‑API от внутреннего **IR графа** и ввести единую точку, через которую будут идти все анализы.

### API и сущности

1. `IRNode`, `IRPort`, `IREdge`, `IRVariable`

   * Простые, сериализуемые структуры:

     ```python
     @dataclass
     class IRNode:
         id: NodeId
         kind: Literal["Raw", "Core", "Ext"]
         reactions: list[IRReaction]

     @dataclass
     class IRReaction:
         id: str
         reads_vars: set[VariableId]
         writes_vars: set[VariableId]
         # позже добавим AST и др.
     ```

2. `CompilerPipeline`

   * Основной класс, управляющий компиляцией:

     ```python
     class CompilerPipeline:
         def __init__(self, config: CompilerConfig): ...
         def build_ir(self, graph: GraphRuntime) -> IRGraph: ...
         def run_passes(self, ir: IRGraph) -> CompileResult: ...
     ```

3. Плагин‑система проходов:

   ```python
   class Pass(ABC):
       name: str
       @abstractmethod
       def run(self, ir: IRGraph, diag: DiagnosticSink) -> None: ...
   ```

   * Примеры пассов:

     * `StructuralPass` (проверка подключений, типов, acyclicity).
     * `WriteConflictPass` (мин‑версия из Этапа 1, но уже на IR).

4. `DiagnosticSink`

   * Сборщик ошибок/предупреждений:

     ```python
     class DiagnosticSink:
         def error(self, code, message, location=None): ...
         def warning(self, code, message, location=None): ...
     ```

### Семантика

* `build_ir` — единственное место, где мы «смотрим на Python мир», отражаем его в IR.
* Все следующие аналитические фичи **работают только через IR** — это позволяет не ломать API нод при добавлении анализа.

### Definition of Done (Этап 2)

* Есть IR‑слой (`IRNode`, `IRGraph`, `IRVariable`).
* `CompilerPipeline` может:

  * собрать IR из графа с `RawNode`,
  * прогнать хотя бы один пасс (`StructuralPass`),
  * выдать `CompileResult` (успех/список ошибок).
* Это уже позволяет:

  * запускать `BestEffort` (минимальные проверки),
  * использовать IR для дебага/визуализации.

> Дальше мы **не меняем** базовую структуру IR (только расширяем поля, но не ломаем смысл сущностей).

---

## Этап 3. Core DSL: `Expr` и `CoreNode` (без сложного анализа)

**Цель:** ввести формально определённый **язык выражений** и класс `CoreNode`, но пока без causality/3‑значной логики; просто типобезопасный AST и интерпретатор.

### API и сущности

1. `Expr[T]` — типизированный AST

   Пример структуры:

   ```python
   class Expr(Generic[T]): ...
   class Const(Expr[T]): value: T
   class Var(Expr[T]):   name: str

   class If(Expr[T]):
       cond: Expr[bool]
       then_: Expr[T]
       else_: Expr[T]

   class BinOp(Expr[T]):
       op: Literal["+", "-", "*", "/", "min", "max"]
       left: Expr[T]
       right: Expr[T]

   class Cmp(Expr[bool]):
       op: Literal["<", "<=", "==", ">", ">="]
       left: Expr[Any]
       right: Expr[Any]
   ```

   Плюс «сахарные» конструкторы: `if_(...)`, `and_`, `or_`, `not_`, `clamp` и т.п.

2. `CoreNode`

   ```python
   class CoreNode(RawNode):
       @abstractmethod
       def build_reactions(self) -> list[CoreReaction]: ...
   ```

   Где `CoreReaction` содержит:

   * список читаемых переменных/портов,
   * AST выражения для каждого выходного `Variable`/`State`.

3. `ExprInterpreter`

   * Интерпретатор `Expr[T]` в обычный Python‑value:

     ```python
     def eval_expr(expr: Expr[T], env: Mapping[str, Any]) -> T: ...
     ```

### Семантика

* Как разработчик, ты **пишешь реакции** через combinator‑API:

  ```python
  class MyNode(CoreNode):
      x = State[float](init=0.0)

      @reaction
      def step(self, x: Expr[float]) -> Expr[float]:
          return x + 1.0
  ```
* Фреймворк превращает этот метод в `Expr`‑AST и сохраняет в `CoreReaction`.

### Definition of Done (Этап 3)

* Можно написать простую `CoreNode` и исполнить её в рантайме через интерпретатор `Expr`.
* IR получает теперь не только «кто что читает/пишет», но и AST реакций.
* Типы выражений проверяются на этапе построения AST (ошибки несоответствия типов ловятся рано).

> После этого этапа структура `Expr` и протокол `CoreNode` **замораживаются**; дальше мы только добавляем новые узлы AST и анализаторы, но не ломаем существующие.

---

## Этап 4. Структурный и write‑конфликт анализ (сверху IR + Core DSL)

**Цель:** на IR‑уровне сделать «жёсткий линтер»: проверки типов, подключения, множественной записи, правильности использования `WritePolicy`.

### Новые проходы

1. `TypeCheckPass`

   * Проверяет, что:

     * типы портов и переменных сопоставимы,
     * все `Input` подключены/имеют default,
     * `Expr`‑деревья типизируются корректно.

2. `WriteConflictPass` (расширенный)

   * Находит для каждой `Variable` множество писателей в одном тике.
   * Если:

     * `write_policy=Error` и писателей > 1 → ошибка;
     * `LWW` без задания порядка → ошибка;
     * моноидная политика (Sum/Max/…) → ок.

3. `StateOwnershipPass`

   * Гарантирует, что не существует двух разных `State` объектов, которые логически соответствуют одной и той же сущности (типа «две версии Account»).

### Definition of Done (Этап 4)

* На любой IR‑граф:

  * **обязателен** запуск `TypeCheckPass` и `WriteConflictPass`;
  * ошибки структурного уровня в принципе нельзя игнорировать в `pragmatic/strict` режимах.
* Документация:

  * чётко описано, какие ошибки относятся к этому уровню (подключения, типы, write‑policy), чтобы не смешивать с causality.

> Начиная с этого этапа, **множественные писатели без политики физически невозможны** в strict‑режиме — это железный гарант.

---

## Этап 5. Causality: SCC + трёхзначная семантика (Core‑SCC)

**Цель:** ввести **формальную проверку каузальности** для Core‑подграфов: обнаруживать алгебраические циклы, проверять конструктивность, запрещать неконструктивные.

### API и сущности

1. `Tag = (t: float | int, micro: int)` — модель superdense time (логический момент).

2. `CausalityPass`

   * Использует:

     * граф зависимостей `Variable`→`Reaction` по instant‑рёбрам;
     * алгоритм Тарьяна для SCC.
   * Для каждого SCC:

     * если размер >1 или self‑loop → это **algebraic cycle**.
     * если в нём только Core/разрешённые Ext → запускаем fixed‑point анализ.

3. Трёхзначные значения

   ```python
   class B3(Enum):  # для булевых
       BOTTOM = "⊥"
       FALSE = "0"
       TRUE  = "1"

   class V3(Generic[T]):  # для данных
       value: T | None
       known: bool
   ```

4. `TernaryInterpreter`

   * `eval_expr_3val(expr, env: Mapping[str, V3]) -> V3`.

5. `ConstructiveCheck`

   * Итерация Клини: от `⊥` к стабильному состоянию.
   * Ошибки:

     * `NonConstructiveError` — переменная остаётся `⊥`;
     * `NonDeterministicError` — два разных известных значения в join‑е.

### Definition of Done (Этап 5)

* Любой Core‑SCC:

  * либо проходит constructive‑анализ и получает schedule/порядок вычисления значений;
  * либо явно помечается как некорректный (ошибка компиляции).
* Некор‑Core/Raw узлы в SCC:

  * либо запрещены в strict‑режиме (требуют delay),
  * либо разрешаются только с явным контрактом (e.g. `monotone=True`).

> Начиная с этого этапа, **неконструктивные instant‑циклы вообще запрещены** в ядре, и это больше не пересматривается.

---

## Этап 6. Init‑анализ, `⊥/absent/present`, clock‑домены

**Цель:** гарантировать, что никто не читает неинициализированные значения (`⊥`), и аккуратно различать “нет значения в этом такте” (`absent`) и “ещё не вычислено” (`⊥`).

### API и сущности

1. Три статуса для сигналов:

   * `⊥` — служебно, только в анализе (non‑causal / неизвестно).
   * `absent` — сигнал легально отсутствует в этом `Tag`.
   * `present(v)` — есть значение `v`.

2. `InitPass`

   * Строит `happens-before` порядок (по фазам + clocks).
   * Для каждого чтения:

     * проверяет, что существует путь записи/инициализации, который однозначно случается раньше.
   * Ошибка: `InitializationError`.

3. Clock‑домены (минимально)

   * У порта/реакции есть `ClockDomain` (например, период/условие).
   * Pass проверяет, что соединения между разными доменами либо:

     * снабжены адаптером (downsample/upsample), либо
     * явно помечены как «async / cross‑domain».

### Definition of Done (Этап 6)

* Любая программа в strict‑режиме:

  * либо гарантированно читает только `present(v)/absent`, но не `⊥`,
  * либо компилируется с ошибками `InitializationError`.
* Документация:

  * чётко объясняет, что `absent` — легитимное состояние, а `⊥` — всегда ошибка.

> Этап 6 **закрывает класс ошибок "читаем мусор/неизвестное"**.

---

## Этап 7. Non‑Zeno и microstep‑шедулинг

**Цель:** ввести семантику microstep‑ов (второй индекс `Tag`) и защиту от Zeno‑поведения.

### API и сущности

1. Расширенный `Scheduler`:

   ```python
   class Scheduler:
       def run_until(self, t_end: float) -> None: ...
       def step_tag(self, tag: Tag) -> None: ...
   ```

   * На каждом `Tag=(t, micro)`:

     * исполняет реактивные события/реакции, пока система не достигнет quiescence или лимита.

2. `NonZenoPass`

   * Анализ SCC на наличие реакций, которые всегда порождают события в том же `Tag` без явного rank.

   * Позволяет аннотировать реакции:

     ```python
     @nonzeno(rank="remaining_steps")
     def enforce(...): ...
     ```

   * Если rank не найден/не указан — warning/ошибка.

3. Runtime‑guard

   * лимит microsteps per `t`:

     ```python
     MAX_MICROSTEPS = 10_000
     ```
   * при превышении → `ZenoRuntimeError` с трассой.

### Definition of Done (Этап 7)

* Scheduler умеет работать с `(t, micro)`, а не только с «плоским» тиком.
* Для strict‑режима:

  * алгебраические SCC либо статически признаны non‑Zeno (по рангу),
  * либо можно поймать потенциально бесконечное поведение runtime‑guard’ом с информативной ошибкой.

> С этого момента временная модель (`Tag=(t, micro)`) и подход к Zeno считаются **каноническими**.

---

## Этап 8. SDF‑подграфы и rate‑анализ (опциональный, но мощный)

**Цель:** дать возможность части графа жить в **Synchronous Dataflow**‑режиме: фиксированные rates, статический schedule, bounded буферы.

### API и сущности

1. Расширения `Input/Output`:

   ```python
   class Input(Generic[T]):
       rate: float | None  # например, 1.0, 0.1 (каждый 10-й), None = DE
   ```

2. `SDFPass`

   * Находит подграфы, где все порты имеют `rate != None`.
   * Строит SDF‑матрицу `Γ` (канал × актор).
   * Решает баланс‑уравнения `Γ·q = 0` (для firing‑вектора).
   * Оценивает boundedness буферов.

3. Ошибки:

   * `SDFInconsistentError` — нет ненулевого решения для `q`.
   * `SDFUnboundedError` — необходим бесконечный буфер.

### Definition of Done (Этап 8)

* Любой SDF‑подграф либо:

  * получает firing‑вектор и расписание/размеры буферов,
  * либо компиляция падает с понятной SDF‑ошибкой.
* Не‑SDF части продолжают жить в DE/SR‑семантике.

> После этого, поддержка SDF — **устойчивая фича**: расширение модели вычислений, не ломающее ядро.

---

## Этап 9. Extended / Raw, контракты, диагностика

**Цель:** формально ввести границы между Core/Ext/Raw и связать их с анализами; сделать удобную диагностику.

### API и сущности

1. `ExtNode` и `RawNode` (доопределение)

   ```python
   class ExtNode(RawNode):
       @reaction
       @contract(
           deterministic=True,
           no_side_effects=True,
           monotone=False,
           no_instant_loop=True,
           max_latency_ms=10,
       )
       def step(self, ctx): ...
   ```

   * `contract` → набор флагов, которыми пользуются анализ‑пасс (например, разрешать/запрещать участие в SCC).

   ```python
   class RawNode(RawNode):
       @reaction
       @unsafe("Reason string")
       def step(self, ctx): ...
   ```

2. Связь с проходами:

   * `CausalityPass`:

     * разрешает Ext‑реакции в SCC только при `monotone=True` и доказуемой совместимости;
     * Raw‑реакции запрещены в SCC strict‑режима.
   * `NonZenoPass`:

     * игнорирует Raw/Ext без аннотаций как небезопасные в strict.

3. Диагностика и отчёты

   * `CompilerReport`:

     * список нод по категориям (Core/Ext/Raw),
     * список ошибок/предупреждений с кодами и привязкой к нодам/переменным,
     * агрегированная сводка (сколько SCC, сколько SDF‑подграфов, есть ли потенциальный Zeno).

### Definition of Done (Этап 9)

* Любой граф можно скомпилировать в одном из режимов:

  * `best_effort` — минимум проверок, Raw/Ext разрешены везде, Core‑анализ можно выключать;
  * `pragmatic` — строгие структурные + causality/init/non‑Zeno для Core; Raw/Ext ограничены;
  * `strict` — максимально строгий режим, где любые нарушения приводят к ошибке.
* Отчёт компиляции:

  * стабилен по формату,
  * содержит достаточно информации для пользователя, чтобы «починить себя».

---

## Как это использовать как roadmap

* Каждый этап — **отдельный релиз/версия**:

  * v0.1 — только Этап 0, есть простой DAG‑рантайм;
  * v0.2 — добавлен Variables/Intents;
  * v0.3 — IR и компиляционная труба;
  * v0.4 — Core DSL (без сложной математики);
  * v0.5 — структурный анализ;
  * v0.6 — causality/fixed‑point;
  * v0.7 — init/clock;
  * v0.8 — non‑Zeno/microsteps;
  * v0.9 — SDF;
  * v1.0 — Ext/Raw/контракты + полная диагностика.

* После каждого этапа:

  * API и семантика **фиксируются**;
  * следующий этап **запрещено** реализовывать, ломая контракты предыдущего.

Так ты получаешь эволюцию от “красивой DAG‑библиотеки” до **формально обоснованного реактивного фреймворка**, где каждая ступень — автономный, полезный deliverable сам по себе.

Если хочешь, дальше могу сделать табличку вида “Этап → новые классы → зависимости от предыдущих → тест‑кейсы”, чтобы это прямо превратить в GitHub‑milestones.
