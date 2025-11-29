

## 1. Какую семантику непрерывного времени мы хотим

У тебя уже есть:

* **DE/SR‑мир**: `Tag = (t, micro)` как superdense‑время, дискретные события, фикc‑пойнт по microstep.
* **SDF и обычные discrete‑reaction ноды**.

**Continuous‑ноды** должны:

1. Описывать **систему ОДУ** вида
   [
   \dot x(t) = f(x(t), u(t), t),
   ]
   где `x` — состояние ноды, `u` — входные сигналы (piecewise‑continuous), `t` — время. ([ptolemy.berkeley.edu][1])
2. Быть совместимыми с твоим `Tag`:

   * глобальное время `t` — вещественное,
   * `micro` — индекс событий **в одном и том же `t`** (разрывы, дискретные переходы). ([ptolemy.berkeley.edu][1])
3. Жить в отдельной **модели вычислений** (CT‑director), как в Ptolemy: CT‑домен внутри общей иерархии с DE/SR. ([ptolemy.berkeley.edu][2])

Стандартный подход (Ptolemy II, HyVisual, Simulink) такой:

* Между дискретными событиями система ведёт себя как **чисто непрерывная** (ODE solver).
* В моменты событий (zero‑crossing, внешние DE‑события) происходит:

  * дискретное обновление состояния (reset),
  * пересчёт структурных/логических частей,
  * возможно изменение динамики (режима). ([ptolemy.berkeley.edu][1])

Мы можем взять ровно эту картинку и встроить её в твой Tag‑мир:
между `Tag=(t_k, micro_last)` и `Tag=(t_{k+1}, 0)` работает CT‑солвер, а все DE‑реакции живут только в точках `t_k`.

---

## 2. Новый слой API: ContinuousNode и ContinuousState

### 2.1. Минимальный интерфейс Continuous‑ноды

Добавляем новый базовый класс:

```python
class ContinuousNode(BaseNode):
    """Нода с непрерывной динамикой dx/dt = f(x, u, t)."""

    # Массив непрерывных состояний
    states: dict[str, ContinuousState]

    def derivative(self, t: float, x: dict[str, float], u: dict[str, float]) -> dict[str, float]:
        """Определяет dx/dt в текущий момент.
        Чистая функция: никаких побочных эффектов, только число → число.
        """
        raise NotImplementedError

    def outputs(self, t: float, x: dict[str, float], u: dict[str, float]) -> dict[str, float]:
        """Моментные выходы, как y = h(x, u, t)."""
        return {}
```

**ContinuousState**:

```python
@dataclass
class ContinuousState:
    name: str
    init: float
    # опционально: ограничения, lipschitz_bound, и т.п.
```

### 2.2. DSL‑вариант для Core‑continuous

На Core‑слое можно дать DSL как мы делали для Expr, но для производной:

```python
class MassSpring(ContinuousNode):
    x = ContinuousState("x", init=0.0)
    v = ContinuousState("v", init=0.0)

    k: float = 1.0
    m: float = 1.0
    c: float = 0.1

    def derivative(self, t, x, u):
        # x, v - значения состояний; u может быть внешней силой
        dxdt = x["v"]
        dvdt = -(self.k / self.m) * x["x"] - (self.c / self.m) * x["v"] + u.get("force", 0.0)
        return {"x": dxdt, "v": dvdt}

    def outputs(self, t, x, u):
        return {"pos": x["x"], "vel": x["v"]}
```

Дальше можно обернуть это Expr‑DSL’ом, но минимально достаточно Python‑функции, если мы признаем её «чёрным ящиком» внутри CT‑солвера.

---

## 3. Как это стыкуется с твоим Tag и DE‑рантаймом

### 3.1. Расширяем Tag

Сейчас у тебя `Tag = (t, micro)` уже есть как класс. Семантика:

* `t` — глобальное физическое время (float), монотонно неубывает.
* `micro` — индекс микрошагов в одном `t` (для instant‑фикспойнта в DE).

Для CT:

* **Непрерывная интеграция** идёт по `t` (0.0 → 0.1 → …), `micro` остаётся 0 внутри интервала.
* **Разрыв / событие** генерит новый `Tag` с тем же `t`, но `micro+1`.

В стиле Lee: CT‑солвер выдаёт *дискретный след* — набор `(t_i, x(t_i))`, достаточный для восстановления траектории между разрывами. ([ptolemy.berkeley.edu][1])

### 3.2. Новый “директор” / режим вычислений

Текущая картинка:

* Есть глобальный Scheduler, который в strict‑режиме решает fixed‑point в дискретных SCC и двигает `t` по тик‑clock’у.

Для CT‑узлов тебе нужен **ContinuousDirector**, примерно так:

```python
class ContinuousDirector:
    def __init__(self, nodes: list[ContinuousNode], edges, config: CTConfig):
        ...

    def integrate(self, t0: float, t1: float, de_events) -> CTIntegrationResult:
        """Интегрирует все continuous-ноды от t0 до t1 (или до ближайшего события)."""
```

Семантика:

* На вход: стартовое состояние всех `ContinuousNode` в момент `t0`, список запланированных DE‑событий, возможно ограничения по шагу.
* На выход: новое состояние к `t1` (или к моменту ближайшего события), плюс набор «zero‑crossing» событий (если есть guards). ([ptolemy.berkeley.edu][2])

Глобальный Scheduler:

1. Берёт текущий `Tag=(t_k, micro_last)`.
2. Определяет ближайшее DE‑событие (по твоему DE‑сценарию).
3. Просит ContinuousDirector интегрировать CT‑подграф до `t_{k+1}` или до zero‑crossing.
4. Превращает zero‑crossing (и внешние DE event’ы) в новые `Tag`’и с `micro>0` и запускает дискретные реакции.

---

## 4. Как Continuous‑ноды видят дискретный мир и наоборот

### 4.1. DE → CT: piecewise constant / piecewise linear

Классическая схема:

* DE‑сигналы (команды, переключатели) считаются **кусково‑постоянными** во времени.
* При каждом DE‑эвенте (т.е. в Tag) ты обновляешь значение параметра/входа у ContinuousNode и дальше используешь его как `u(t)` до следующего события. ([ptolemy.berkeley.edu][2])

Реализация:

* Для каждого DE‑выхода, идущего в CT‑вход:

  * на стороне CT‑директора хранится «текущее значение» этого входа,
  * при DE‑событии — обновляется, и CT‑солвер видит новое `u` с момента `t_event`.

Более продвинутый вариант — piecewise linear: DE присылает не только значение, но и «скорость» или ключевые точки, но это можно сделать позже.

### 4.2. CT → DE: sample + threshold crossing

В обратную сторону:

1. **Сэмплинг по времени** — DE‑нода «спрашивает» ContinuousNode значение при `t_k` (например, каждый тик 0.01). Простейший адаптер: `SamplerNode`, который на входе имеет continuous, на выходе — discrete.
2. **События по guard’ам** — ContinuousNode следит за условиями типа `g(x,u,t) = 0` (zero‑crossing). При пересечении:

   * CT‑директор находит точное время события (маленький root‑finder),
   * выдаёт DE‑event на соответствующий порт (новый Tag с этим `t`, `micro++`),
   * возможно сразу делает reset состояния (hybrid transition). ([ptolemy.berkeley.edu][2])

API для guard’ов:

```python
class ContinuousNode(BaseNode):
    def guards(self, t, x, u) -> dict[str, float]:
        """Каждый guard = значение g(t,x,u); событие при смене знака."""
        return {}
```

---

## 5. Алгебраические петли и CT‑алгебраические петли

В CT‑домене есть ровно та же проблема, что и в Simulink/Modelica:

* если выход блока **с прямой проводимостью** (direct feedthrough) идёт назад на его вход *без задержки*, то в каждом шаге интегратора нужно решить **алгебраическое уравнение/систему**. ([mathworks.com][3])

Семантически это:

* **DAE**:
  [
  0 = h(x(t), u(t), y(t)), \quad \dot x(t) = f(x(t), y(t), t),
  ]
  где `y` — алгебраические переменные.

В твоём фреймворке это можно встроить так:

1. **Structural detection** (похож на твой CausalityPass, но для CT‑портов):

   * строишь граф зависимости `output→input` для CT‑портов с direct feedthrough,
   * находишь SCC (алгебраические циклы).

2. Для таких SCC:

   * либо **запрещаешь** их в strict‑режиме и просишь пользователя вставить explicit delay / state (как Memory в Simulink),
   * либо делаешь отдельный *CT‑algebraic solver* (Newton на каждом шаге интегратора). ([mathworks.com][3])

На первых итерациях проще:

* объявить, что continuous‑ноды **не имеют direct feedthrough** между собой (выход зависит только от состояний),
* либо ограничить петли, чтобы они всегда проходили через `ContinuousState` (аналог delay).

---

## 6. Non‑Zeno в гибридном мире

Zeno в гибридных системах:

* пример — *прыгающий шар*, где время между отскоками уменьшается → бесконечно много событий за конечное время. ([ptolemy.berkeley.edu][1])

Для твоего фреймворка:

1. На уровне дизайна ContinuousNode:

   * хорошо бы дать пользователю возможность задать **минимальный dwell time** для guard’а:

     ```python
     @guard(min_dwell=1e-3)
     def hit_ground(...): ...
     ```
   * либо явно писать, что `guard` не должен триггериться мгновенно после своего reset’а.

2. На уровне runtime:

   * уже есть NonZeno‑guard для DE (лимит microsteps на `t`).
   * для CT+DE можно использовать тот же лимит: если за один physical `t` (с точки зрения EPS-погрешности) происходит > N событий → считать это Zeno и падать с ошибкой.

3. На уровне анализа:

   * можно консервативно проверять, что последовательность guard’ов не может порождать бесконечный цикл без прогресса по состоянию (сложно, но для примитивных кейсов возможно).([SpringerLink][4])

---

## 7. Как всё это встраивается в твой staged‑план

Предлагаю считать поддержку continuous ещё **отдельной стадией** (скажем, Stage 10), чтобы не ломать уже замороженное:

### 7.1. API‑слой (Stage 10A)

Deliverable:

* `ContinuousNode` базовый класс.
* `ContinuousState` тип.
* Адаптеры:

  * `SamplerNode` (CT→DE),
  * `ZeroOrderHold` (DE→CT).

Definition of Done:

* Можно написать чисто continuous‑подграф (пара ODE‑нод) и интегрировать его от t0 до t1 с фиксированным шагом.
* Можно подключить DE‑узел через ZeroOrderHold и считывать continuous‑сигнал через SamplerNode.

### 7.2. IR + Compiler (Stage 10B)

Deliverable:

* `IRNode.kind = "Continuous"`,
* добавление CT‑метаданных в IR:

  * список continuous‑states,
  * `derivative`/`outputs` как ссылки/AST,
  * список `guards`.
* `ContinuousPass`:

  * выделяет CT‑подграфы,
  * проверяет, что нет нелегальных смешанных connections без адаптеров,
  * проверяет отсутствие/разрешимость CT‑алгебраических циклов.

### 7.3. Runtime‑директор (Stage 10C)

Deliverable:

* `ContinuousDirector` с простым численным интегратором (например, RK4 с фиксированным шагом).
* Интеграция в основной `Scheduler`:

  ```python
  while t < t_final:
      t_next_de = next_discrete_event_time()
      ct_result = ct_director.integrate(t, t_next_de, de_inputs)
      t = ct_result.t_end
      apply_ct_state(ct_result)
      process_de_events(ct_result.events)
  ```

Definition of Done:

* Есть демонстрационный гибридный пример (типа термостата или mass‑spring + логический контроллер), который:

  * даёт ожидаемую траекторию,
  * формально согласуется с твоим Tag‑миром (`t` и `micro`),
  * корректно обрабатывает переключения (guards).

---

## 8. Нюансы и компромиссы

1. **Численная точность vs. формальная семантика.**
   Ты можешь:

   * на Core‑уровне говорить о “идеальных” ODE,
   * а в runtime использовать стандартный численный метод с допуском (tolerances).
     В литературе это описывают как “executable hybrid models”, где семантика ориентирована на то, что можно реально выполнить. ([SpringerLink][4])

2. **Сколько анализировать statically?**

   * Существование/единственность решения (Липшицевость) — по‑настоящему доказать сложно; максимум — **декларации пользователя** (`lipschitz_bound`, `domain_bounds`) и sanity‑checks. ([itslearningakarmazyan.files.wordpress.com][5])
   * Алгебраические петли в CT — уровень структурного анализа (как сейчас в CausalityPass) + запрет “опасных” конфигураций.

3. **Взаимодействие с SDF.**

   * SDF‑подграфы и CT‑подграфы лучше держать **раздельно** как разные модели вычислений.
   * Если SDF‑узел читает CT‑сигнал — это фактически Sampler; если CT читает SDF‑сигнал — ZeroOrderHold или более сложный интерполятор.

---

## Термины

* **Continuous‑time node / ContinuousNode** — узел, описывающий ОДУ `dx/dt=f(x,u,t)`; вычисляется численным интегратором.
* **Hybrid system** — система с совместным непрерывным (`x(t)`) и дискретным (`events`) поведением. ([SpringerLink][4])
* **Continuous director** — компонент, управляющий CT‑подграфом (ODE solver, event‑handling), как в Ptolemy II. ([ptolemy.berkeley.edu][2])
* **Zero‑crossing / guard** — условие вида `g(x,u,t)=0`, по пересечению которого генерится дискретное событие.
* **Algebraic loop (CT)** — цикл мгновенных зависимостей между сигналами непрерывного времени, приводящий к алгебраическим уравнениям/DAE. ([mathworks.com][3])
* **Hybrid Zeno** — бесконечное число дискретных событий за конечное физическое время (например, бесконечные отскоки). ([ptolemy.berkeley.edu][1])


**Переформулировка / Restated**

* **EN.** *Give a concrete, staged spec for adding continuous‑time nodes to our framework, where each stage delivers a small, self‑contained, “frozen” piece: core classes, interfaces, methods and Definition of Done. After a stage is done, later stages only build on it, not redesign it.*
* **RU.** *Дай покомпонентную спецификацию поддержки continuous‑нод, разбитую на этапы, где каждый этап даёт осязаемый, завершённый результат (классы/типы/интерфейсы/методы + Definition of Done). После завершения этапа он считается базой и дальше не меняется, на него только наслаиваются следующие уровни.*

---

## Общая картина: continuous как ещё один “директор”

Мы принимаем как основание модель Ptolemy/Simulink: continuous‑подграф управляется отдельным **CT‑директором** (ODE‑solver + обработка событий), который интегрируется с глобальным DE/SR‑scheduler’ом через единое время `t` и superdense Tag `(t, micro)`.[¹](#ref1)[²](#ref2)

Ниже — **три этапа (10A, 10B, 10C)**, каждый с:

* набором новых сущностей (классы/типы/методы),
* инвариантами,
* Definition of Done (что должно быть написано/протестировано).

---

## Этап 10A — “CT‑ядро”: ContinuousNode + ContinuousState + локальный интегратор

**Цель:** появиться должна возможность написать **чисто continuous‑подграф**, без DE, и прогнать его вперёд по времени.

### 10A.1. Новые типы

#### (1) `ContinuousState[T]`

```python
class ContinuousState(Generic[T]):
    def __init__(self, init: T):
        self.init = init
        self.name: str | None = None    # устанавливается при регистрации в узле
```

**Инварианты:**

* Имеет **обязательное** начальное значение `init`.
* Не знает ничего про интегратор/время — это просто декларация “у этого узла есть continuous‑состояние x(t)”.

#### (2) `ContinuousNode`

Базовый класс для ODE‑узла:

```python
class ContinuousNode(BaseNode):
    # Локальные continuous‑состояния
    continuous_states: dict[str, ContinuousState]

    # Настройки интегратора (пока фиксированные, потом можно расширить)
    integrator: Literal["euler", "rk4"] = "rk4"
    max_step: float = 0.01

    def derivative(self, t: float, x: dict[str, float]) -> dict[str, float]:
        """
        dx/dt = f(t, x). Пока без входов u(t) и параметров — только чистая ОДУ.
        Должна быть чистой функцией (без сайд-эффектов).
        """
        raise NotImplementedError

    def outputs(self, t: float, x: dict[str, float]) -> dict[str, float]:
        """
        y = g(t, x). Тоже чистая функция.
        """
        return {}
```

На этом этапе **нет входов/выходов в граф**, это чистый CT‑компонент.

#### (3) `ContinuousDirector` (локальный)

```python
class ContinuousDirector:
    def __init__(self, nodes: list[ContinuousNode]):
        self.nodes = nodes
        self.t: float = 0.0
        self.x: dict[ContinuousNode, dict[str, float]] = {
            n: {name: state.init for name, state in n.continuous_states.items()}
            for n in nodes
        }

    def step(self, dt: float) -> None:
        """Сделать один шаг интеграции по всем узлам."""
        for node in self.nodes:
            x_cur = self.x[node]
            # Выбор метода интеграции
            if node.integrator == "euler":
                dx = node.derivative(self.t, x_cur)
                x_next = {k: x_cur[k] + dt * dx[k] for k in x_cur}
            elif node.integrator == "rk4":
                # классический RK4 (можно вынести в helper)
                x_next = rk4_step(node.derivative, self.t, x_cur, dt)
            self.x[node] = x_next
        self.t += dt

    def run_until(self, t_end: float, max_step: float | None = None) -> None:
        """Интегрировать до t_end с шагом <= max_step."""
        max_step = max_step or min(n.max_step for n in self.nodes)
        while self.t < t_end:
            dt = min(max_step, t_end - self.t)
            self.step(dt)
```

**Инварианты:**

* `self.x[node]` всегда содержит одно значение для каждого continuous‑состояния ноды.
* `self.t` монотонно возрастает.
* ОДИН ContinuousDirector может управлять несколькими независимыми узлами (без связи между ними на этом этапе).

### 10A.2. Definition of Done (10A)

1. **API готово и задокументировано**:

   * `ContinuousState(init=...)` — понятен и стабилен.
   * `ContinuousNode.derivative/outputs` — явно описаны как чистые функции.
   * `ContinuousDirector` — управляет временем и состояниями, не знает ни про DE, ни про Variables.

2. **Тесты:**

   * Простейшая ОДУ `dx/dt = 1`, `x(0) = 0`:

     * `run_until(1.0)` даёт примерно `x ≈ 1` (с ожидаемой погрешностью).
   * Два узла (например, `dx/dt = v`, `dv/dt = -x` — гармонический осциллятор), проверка, что энергия примерно сохраняется.
   * Проверка, что шаг `max_step` влияет на точность, но не ломает монотонность времени.

> После 10A мы **не меняем** интерфейс `ContinuousState`, `ContinuousNode` и базовый контракт `ContinuousDirector.step/run_until`; максимум — добавляем новые интеграторы и опции.

---

## Этап 10B — “CT↔DE мост”: интеграция ContinuousDirector в GraphRuntime

**Цель:** привязать CT‑ядро к уже существующему DE/SR‑рантайму: теперь CT‑состояния и выходы должны участвовать в обычной `run_until`/`run_tick`, используя единое время `t` и твой Tag‑механизм.[¹](#ref1)[²](#ref2)

### 10B.1. Расширение GraphRuntime

Добавляем:

```python
class GraphRuntime:
    def __init__(...):
        ...
        self.continuous_nodes: list[ContinuousNode] = []
        self.continuous_director = ContinuousDirector(self.continuous_nodes)
        self.current_time: float = 0.0  # глобальное физическое время

    def add_node(self, node: BaseNode):
        ...
        if isinstance(node, ContinuousNode):
            self.continuous_nodes.append(node)
```

### 10B.2. Новый метод `run_until(t_end: float)`

```python
def run_until(self, t_end: float):
    """Продвинуть систему по времени до t_end (не обязательно целого числа шагов)."""
    while self.current_time < t_end:
        # 1) определяем следующий DE-событие (пока просто: t_next = t_end)
        t_target = t_end

        # 2) Продвигаем continuous-часть
        self.continuous_director.run_until(t_target)

        # 3) Обновляем current_time
        self.current_time = self.continuous_director.t

        # 4) Делаем один DE-тактовый шаг на t_target
        self._run_tick_at_time(self.current_time)

def _run_tick_at_time(self, t: float):
    """
    Исполнить все дискретные ноды (Raw/Core/Ext) в момент времени t.
    Continuous-ноды к этому моменту уже интегрированы до t.
    """
    # 4.1) Выписать continuous-outputs в DE-порты
    for node in self.continuous_nodes:
        x = self.continuous_director.x[node]
        y = node.outputs(t, x, inputs={})  # пока без u(t)
        self._write_continuous_outputs_to_ports(node, y)

    # 4.2) Запустить обычный DE/SR-цикл по schedule (как в нынешнем run_step/run_tick)
    self._run_discrete_tick_at(t)
```

На этом подэтапе **нет** DE→CT обратного влияния (u(t)), только CT→DE.

### 10B.3. DE→CT: ZeroOrderHoldNode

Чтобы дискретные ноды могли задавать вход continuous‑миру, вводим адаптер:

```python
class ZeroOrderHoldNode(CoreNode or ExtNode):
    """Адаптер: DE → CT (piecewise constant)."""
    u_in = Input[float]()
    u_out = Output[float]()  # continuous view

    state = State[float](init=0.0)

    @reaction
    def remember(self, u: float):
        self.state.set(u)
        self.u_out.emit(u)
```

Для continuous‑директора мы будем использовать **состояние** `state` как вход `u(t)` на всём интервале `(t, t_next)`.

В `ContinuousDirector`:

```python
def env_inputs(self, t: float) -> dict[ContinuousNode, dict[str, float]]:
    """
    Собрать значения входов u(t) для всех continuous-нод.
    Сейчас: из DE-Variables/State, которые мы считаем piecewise constant.
    """
```

### 10B.4. Continuous→DE: SamplerNode

Адаптер для sample‑инга continuous‑выхода в DE‑мир:

```python
class SamplerNode(CoreNode):
    cont_in = Input[float]()    # continuous-порт (y(t))
    disc_out = Output[float]()  # discrete-порт

    @reaction
    def sample(self, y: float):
        self.disc_out.emit(y)
```

Смысл: на каждом DE‑тикe при времени `t_k` continuous‑директор уже интегрировал до `t_k`, и `disc_out` просто выдаёт `y(t_k)`.

### 10B.5. Definition of Done (10B)

1. `GraphRuntime.run_until(t_end)`:

   * корректно вызывает `ContinuousDirector.run_until(t_target)` между DE‑тиками,
   * затем исполняет дискретный шаг на `t_target`, где все continuous‑outputs доступны на DE‑портах.

2. Можно построить следующий сценарий:

   * Continuous‑маятник (`ContinuousNode` с `dx/dt=v, dv/dt=-x`),
   * SamplerNode, который каждые `dt=0.1` тика отдаёт `x(t)` в дискретную ноду,
   * дискретная нода логирует `x(t)` → получаем разумную траекторию.

3. ZeroOrderHold:

   * дискретная нода управляет параметром `force`;
   * ZeroOrderHold делает его piecewise‑constant;
   * ContinuousNode корректно реагирует на изменения `force` в нужные моменты времени.

> После 10B, отношение “DE‑шаг ⇒ обновить u(t), затем continuous интеграция до следующий DE‑события” становится **фиксированным контрактом**.

---

## Этап 10C — Гибридные события: guards, reset, Zeno‑защита (MVP)

**Цель:** дать возможность continuous‑ноду генерить дискретные события (hybrid events) и делать reset состояния при пересечении guard’ов, без ухода в бездну общих гибридных автоматов.[²](#ref2)[³](#ref3)

### 10C.1. Guard API

Расширяем `ContinuousNode`:

```python
@dataclass
class CTEvent:
    name: str
    data: dict[str, float] | None = None

class ContinuousNode(BaseNode):
    ...

    def guards(self, t: float, x: dict[str, float], u: dict[str, float]) -> dict[str, float]:
        """
        g_i(t,x,u): значения guard-функций.
        Событие происходит, когда g_i меняет знак (обычно crossing через 0).
        """
        return {}

    def on_event(self, event: CTEvent, t: float, x: dict[str, float]) -> dict[str, float]:
        """
        Reset-сема: получает событие и состояние до события,
        возвращает новое состояние x+.
        """
        return x
```

### 10C.2. Поддержка zero‑crossing в ContinuousDirector

В `ContinuousDirector.advance_to(t_target, ...)`:

1. Внутри шага интегратора, при переходе `t → t+dt`:

   * вычисляем `g_old = guards(t, x_old, u_old)`,
   * прогнозируем `x_new` (предварительный шаг),
   * вычисляем `g_new = guards(t+dt, x_new, u_new)`.

2. Если для какого‑то guard’а `g_old * g_new < 0`:

   * произошло crossing → нужно найти `t_event ∈ (t, t+dt)`;
   * делаем простой двоичный поиск или secant до заданной точности;
   * интегрируем до `t_event`, получаем `x_event`;
   * создаём `CTEvent(name=guard_name, data=...)`.

3. После события:

   * вызываем `new_x = node.on_event(event, t_event, x_event)`,
   * заменяем `x_event → new_x` как текущее состояние,
   * добавляем DE‑event в очередь глобального Scheduler’а (чтобы дискретные ноды могли отреагировать на `Tag=(t_event, μ++)`).

### 10C.3. Глобальный Scheduler и Tag

Переопределяем логику `run_until`:

```python
def run_until(self, t_end: float):
    while self.current_time < t_end:
        # 1) узнаём ближайшее запланированное DE-событие (например, плановый тик или внешнее событие)
        t_next_de = self._next_planned_de_time()
        t_target = min(t_next_de, t_end)

        # 2) ContinuousDirector интегрирует до t_target или до ближайшего CT-события:
        ct_result = self.continuous_director.integrate_until(
            t_target,
            env_inputs=self._inputs_for_continuous,
        )
        self.current_time = ct_result.t

        # 3) Если были CT-события → превратить их в DE-события (Tag=(t, micro++))
        for ev in ct_result.events:
            self._enqueue_de_event_from_ct(ev)

        # 4) Исполнить все DE-события при self.current_time
        self._run_all_de_events_at(self.current_time)
```

**Инварианты:**

* `current_time` всегда совпадает с `ContinuousDirector.t`.
* Все CT‑события превращаются в DE‑события **в том же времени `t`**, но с увеличением `micro`, чтобы соблюсти superdense‑семантику.¹[¹](#ref1)[²](#ref2)

### 10C.4. Zeno‑защита для гибридной части

Вводим:

* `ct_event_count_at_t: dict[float, int]` — сколько CT‑событий произошло при каждом `t`.
* Конфиг `MAX_CT_EVENTS_PER_TIME` (например, 1000).

В `ContinuousDirector`:

```python
if ct_event_count_at_t[t_event] > MAX_CT_EVENTS_PER_TIME:
    raise HybridZenoError(
        f"Too many CT events at t={t_event}, potential Zeno behavior"
    )
```

Документируем:

* В strict‑режиме это ошибка;
* В pragmatic‑режиме — можно сделать warning + прерывание интеграции.

### 10C.5. Definition of Done (10C)

1. **Guard/Reset API** реализованы:

   * ContinuousNode может объявить `guards` и `on_event`.
   * ContinuousDirector корректно обнаруживает crossing и вызывает reset.

2. **DE‑интеграция**:

   * CT‑события создают DE‑events, которые запускают дискретные реакции (например, `if event.name == "hit_ground": reflect velocity` в другой ноде).

3. **Zeno‑защита**:

   * В тесте типа “прыгающий шар с коэффициентом упругости <1” — после некоторого числа ударов поднимается `HybridZenoError` (или логируется предупреждение), вместо того чтобы scheduler входил в бесконечный цикл.

4. **Документация**:

   * описано, что continuous‑ноды **не** гарантируют формальной Zeno‑свободы; это частично runtime‑контроль, частично ответственность пользователя (например, добавлять минимальный dwell‑time самими guard’ами).

> После 10C гибридная поддержка **стабилизирована**: интерфейсы `guards/on_event`, взаимодействие с Tag и Zeno‑ограничения считаются каноничными.

---

## Мини‑резюме по этапам

* **10A:**
  ContinuousNode/ContinuousState/ContinuousDirector как **отдельный ODE‑мир**, без DE.
  → можно интегрировать чистую continuous‑модель.

* **10B:**
  Интеграция CT↔DE: GraphRuntime.run_until(t_end), Sampler (CT→DE), ZeroOrderHold (DE→CT).
  → continuous‑узлы становятся частью общего графа, DE‑ноды могут их читать/управлять ими.

* **10C:**
  Гибридные события (guards, reset), CT‑события превращаются в DE‑Tag‑события, Zeno‑guard.
  → полноценный гибридный MoC, совместимый с твоей SR/DE‑семантикой и Tag‑моделью.

Каждый этап даёт **самостоятельный deliverable**, который спокойно можно выпускать как отдельную минорную версию, а потом только расширять вокруг, не ломая базовые классы.
