**Restated Question / Переформулировка запроса**

* **EN.** *Invent a small but non‑trivial pipeline that, on our “ideal” framework, showcases as many of the features we designed as possible. First show a plausible, naive version in a very free style that the compiler accepts in a lenient mode but that behaves incorrectly. Then switch to the strict/fully featured mode, and let the compiler, step by step, point out issues; after each fix we converge to a correct pipeline. The story should feel like the compiler “beats the developer’s stupidity”.*
* **RU.** *Придумай небольшой, но нетривиальный пайплайн, который на “идеальном” фреймворке демонстрирует максимум наших фич. Сначала наивный правдоподобный вариант в максимально свободном стиле, который компилятор в мягком режиме пропускает, но он работает не так, как задумано. Затем разработчик включает строгий режим и по шагам исправляет код по подсказкам компилятора, пока не получается корректный пайплайн — чтобы было ощущение, что компилятор “победил тупость разработчика”.*

---

## Сценарий: RL‑бэктест с плечом и маржин‑коллом

Домейн: **RL‑агент торгует фьючами с плечом** в симуляции. На каждом тике:

1. `PriceFeed` даёт цену.
2. `Strategy` → действие `raw_action` (из RL‑модели).
3. `MarketSim` применяет действие → обновляет `account_state`.
4. `RiskManager` проверяет маржу; при необходимости добавляет принудительные ликвидации.
5. `ExperienceCollector` записывает `(s, a, r, s')` для тренировки.
6. `Trainer` иногда делает шаг обучения.

Внутри тика нам **важно**: маржин‑колл должен сработать **после** применения действий, но **ещё в этом же тике** — микро‑цикл `(t, μ)`.

---

# Вариант 0: Наивный, “как в обычном Python”

### Код (понятный, но без формальных гарантий)

```python
class Account:
    def __init__(self):
        self.cash = 10_000
        self.position = 0
        self.equity = 10_000

global_account = Account()

class PriceFeed(RawNode):
    price = Output[float]()

    @reaction
    def step(self, ctx):
        ctx.write(self.price, get_next_price())  # произвольная логика


class Strategy(RawNode):
    price = Input[float]()
    action = Output[float]()  # позиция Δ

    @reaction
    def step(self, ctx):
        p = ctx.read(self.price)
        # пользуемся ГЛОБАЛЬНЫМ состоянием (грубый стиль)
        obs = (p, global_account.position, global_account.equity)
        a = rl_policy(obs)          # NN, произвольный Python
        ctx.write(self.action, a)


class MarketSim(RawNode):
    price = Input[float]()
    action = Input[float]()

    @reaction
    def step(self, ctx):
        p = ctx.read(self.price)
        a = ctx.read(self.action)

        # Сразу мутируем global_account
        cost = a * p
        global_account.position += a
        global_account.cash -= cost
        global_account.equity = (
            global_account.cash + global_account.position * p
        )


class RiskManager(RawNode):
    margin_call = Output[bool]()

    @reaction
    def step(self, ctx):
        # Читает уже изменённый global_account
        eq = global_account.equity
        margin = eq / abs(global_account.position) if global_account.position else float("inf")

        if margin < 500:  # условный уровень маржи
            # Маржин‑колл: принудительно нулим позицию
            forced_cost = -global_account.position * last_price()
            global_account.cash -= forced_cost
            global_account.position = 0
            global_account.equity = global_account.cash
            ctx.write(self.margin_call, True)
        else:
            ctx.write(self.margin_call, False)


class ExperienceCollector(RawNode):
    price = Input[float]()
    action = Input[float]()
    margin_call = Input[bool]()

    @reaction
    def step(self, ctx):
        p = ctx.read(self.price)
        a = ctx.read(self.action)
        mc = ctx.read(self.margin_call)
        reward = global_account.equity  # просто для примера
        buffer.append((p, a, reward, mc))


class Trainer(RawNode):
    @reaction
    def step(self, ctx):
        if len(buffer) > 1024:
            train_rl(buffer)
            buffer.clear()
```

### Почему “работает”, но плохо

В **best_effort** режиме компилятор делает только поверхностную проверку:

* порты подключены,
* типов нетривиальных конфликтов,
* нет явного self‑loop в графе нод.

Он **не знает** про `global_account` и не видит algebraic loop; порядок исполнения (`Strategy → MarketSim → RiskManager → Experience → Trainer`) — просто топосорт по связям.

**Симптомы:**

* Поведение зависит от **произвольного порядка** нод: если поменять порядок Risk/Market местами → совершенно другая динамика.
* Маржин‑колл уже мутирует `global_account`, а Experience читает его без понятия, что было “до” и “после” ликвидации.
* Никакого понятия о `(t, μ)` — всё происходит “как получится”.

Разработчик видит странные результаты и говорит:

> “Ок, давай включим Strict‑режим и посмотрим, что компилятор скажет”.

---

# Шаг 1: Включаем строгий режим — первая пощёчина

```python
exe = g.compile(mode="strict")
```

### Диагностика компилятора

Компилятор смотрит на зависимости:

* Strategy читает `global_account.*` (через скрытую глобальную)
* MarketSim пишет `global_account.*`
* RiskManager и Experience читают/пишут те же поля

и строит **граф мгновенных зависимостей** (по сути, через Variable `account_state`, которую он должен увидеть как “shared”).

В strict‑режиме runtime/аналитика говорят:

> **Error S001: RawNode in zero-delay cycle on shared state `global_account`**
>
> Detected an instantaneous feedback loop:
> `Strategy → MarketSim → RiskManager → Experience`
> all reading/writing the same global state without an explicit delay or state variable.
>
> Strict mode requires:
> • either model shared state as `State[Account]` with controlled writes,
> • or break the cycle with an explicit delay,
> • or move this logic into Core DSL to enable fixed-point analysis.

**Инсайт:** глобальное состояние нужно сделать явным `State`, работать через intents и commit‑фазу.

---

# Шаг 2: Переносим состояние в State + Intents (но ещё без Core)

Мы переписываем на Extended‑слой (ещё не Core), но уже **без глобалей** и с явным `State`.

```python
class AccountState:
    cash: float
    position: float
    equity: float

class AccountNode(ExtNode):
    state = State[AccountState](init=AccountState(10_000, 0, 10_000))


class Strategy(ExtNode):
    price = Input[float]()
    account = Input[AccountState]()
    action = Output[float]()

    @reaction
    def step(self, price: float, account: AccountState) -> float:
        obs = (price, account.position, account.equity)
        return rl_policy(obs)


class MarketSim(ExtNode):
    price = Input[float]()
    action = Input[float]()
    account = Input[AccountState]()
    account_out = Output[AccountState]()   # пишет назад

    @reaction
    def step(self, price: float, action: float, acc: AccountState) -> AccountState:
        cost = action * price
        new_pos = acc.position + action
        new_cash = acc.cash - cost
        new_eq = new_cash + new_pos * price
        return AccountState(new_cash, new_pos, new_eq)


class RiskManager(ExtNode):
    account = Input[AccountState]()
    margin_call = Output[bool]()
    account_out = Output[AccountState]()

    @reaction
    def step(self, acc: AccountState) -> tuple[bool, AccountState]:
        if acc.position == 0:
            return False, acc
        margin = acc.equity / abs(acc.position)
        if margin < 500:
            # ликвидация
            new_cash = acc.equity
            new_acc = AccountState(new_cash, 0, new_cash)
            return True, new_acc
        else:
            return False, acc
```

**В графе** теперь явно:

* `AccountNode.state` → читается Strategy, MarketSim, RiskManager
* MarketSim и RiskManager **оба** пишут `account_out` (которое потом вернётся в AccountNode.state через commit).

Компилятор в strict‑режиме уже не ругается на глобали, но выдаёт новое:

> **Error S002: Multiple writers for `AccountNode.state` without merge policy**
>
> Writers in same logical tick:
> • MarketSim.account_out
> • RiskManager.account_out
>
> Strict mode requires a deterministic merge policy:
> • define `write_policy` on `AccountNode.state`, or
> • refactor so there is a single writer (e.g., introduce `effective_action` variable).

Мы как разработчик:

> “А, точно, и MarketSim, и RiskManager пытаются обновить аккаунт одновременно. Надо разделить ответственность”.

---

# Шаг 3: Разделяем ответственность и вводим намерения (Intents)

Мы делаем более правильную модель:

* Strategy → генерирует **намерения** `OrderIntent`.
* MarketSim — единственный, кто обновляет `AccountState` на основании **списка intentions**.
* RiskManager больше НЕ пишет в аккаунт, а добавляет **дополнительные intentions ликвидации**.

Core‑DSL нам понадобится именно в этом микрoцикле:
`MarketSim` и `RiskManager` должны *совместно* выйти на фикс‑пойнт по списку ордеров и счёту в рамках одного `t` (например, сначала действия стратегии, затем при необходимости ликвидация).

### 3.1. Моделируем Intents

```python
@dataclass
class OrderIntent:
    size: float  # Δпозиции
    reason: Literal["strategy", "risk_liquidation"]
```

### 3.2. Обновлённые ноды (с упором на Core там, где нужен фикс‑пойнт)

```python
class Strategy(CoreNode):
    price = Input[float]()
    account = Input[AccountState]()
    orders = Output[OrderIntent]()

    @reaction
    def propose(self, price: Expr[float], acc: Expr[AccountState]) -> Expr[OrderIntent]:
        # Core DSL: строим AST, а не выполняем Python
        desired = compute_desired_delta(price, acc)   # Expr[float]
        return OrderIntentExpr(size=desired, reason="strategy")


class RiskManager(CoreNode):
    account = Input[AccountState]()
    orders_in = Input[OrderIntent]()   # поток от Strategy / себя
    orders_out = Output[OrderIntent]()

    @reaction
    def enforce(self, acc: Expr[AccountState], order: Expr[OrderIntent]) -> Expr[OrderIntent]:
        # Если маржа ок — пропускаем ордер, иначе добавляем ликвидационный
        new_acc = simulate_single_order(acc, order)  # Expr[AccountState], без side-effects

        margin_ok = compute_margin_ok(new_acc)       # Expr[bool]

        safe_order = if_(
            margin_ok,
            order,        # исходный
            OrderIntentExpr(size=-acc.position, reason="risk_liquidation")
        )

        return safe_order
```

```python
class MarketSim(CoreNode):
    price = Input[float]()
    account = State[AccountState](init=AccountState(...))
    orders = Input[OrderIntent]()   # после RiskManager
    account_out = Output[AccountState]()

    @reaction
    def apply(self, price: Expr[float], acc: Expr[AccountState], order: Expr[OrderIntent]) -> Expr[AccountState]:
        new_acc = simulate_single_order(acc, order)
        self.account.set(new_acc)
        return new_acc
```

Теперь:

* Единственный writer `AccountNode.state` — это `MarketSim.account`.
* `Strategy` и `RiskManager` **вообще не пишут** в аккаунт; они только манипулируют списком ордеров.

Компилятор снова запускается в strict‑режиме.

### Что говорит компилятор теперь

**Ошибка другая:**

> **Error C003: Non-constructive instantaneous cycle on `OrderIntent`**
>
> Found algebraic SCC involving CoreNode reactions:
> • Strategy.propose
> • RiskManager.enforce
> • MarketSim.apply
>
> The value of `orders` depends on itself within the same logical time without an explicit delay or a provably convergent fixed point.
>
> Suggestions:
> • introduce a delay (use previous tick’s orders), or
> • ensure `RiskManager.enforce` is monotone & inflationary in a suitable lattice and re-run constructive causality check, or
> • constrain the SCC so that only MarketSim is in the cycle and RiskManager runs in a separate microstep.

Компилятор “поймал” то, что мы, как разработчики, **не до конца подумали**: сейчас у нас есть потенциальный цикл:

* Strategy → даёт order
* Risk → может заменить его на ликвидационный → это опять поступает в Market → обновляет аккаунт → может вызвать новый risk‑order и т.д. — без чёткого fixed‑point/ограничения.

---

# Шаг 4: Разруливаем фикс‑пойнт (делаем Core‑часть конструктивной)

Мы решаем:

* В одном `t` мы допускаем **не более одного шага** ликвидации.
* Семантика: Risk делает **одну** проверку поверх изначальных ордеров стратегии и текущего аккаунта; если не ок — форсит ликвидацию **вместо** исходного ордера, но не делает многократных микрошагов.

То есть мы, по сути, говорим: “никакого вложенного фикс‑пойнта, это обычный комбинационный блок”.

Переписываем `RiskManager` так, чтобы он **не зависел от себя/MarketSim** внутри тика, а только от “до‑рыночного” аккаунта:

```python
class RiskManager(CoreNode):
    pre_trade_account = Input[AccountState]()  # снимок до применения ордера
    order_in = Input[OrderIntent]()
    order_out = Output[OrderIntent]()

    @reaction
    def enforce(self, acc0: Expr[AccountState], order: Expr[OrderIntent]) -> Expr[OrderIntent]:
        # симулируем применение ордера на аккаунт ДО сделки
        acc1 = simulate_single_order(acc0, order)
        margin_ok = compute_margin_ok(acc1)

        return if_(
            margin_ok,
            order,
            OrderIntentExpr(size=-acc0.position, reason="risk_liquidation")
        )
```

И меняем wiring:

* `pre_trade_account` → берём из `MarketSim.account` **с задержкой** (значение на входе тика).
* `MarketSim.apply` теперь применяет только уже `enforced` ордер.

Теперь algebraic SCC между `Strategy`/`Risk`/`Market` **исчезает**: по instant‑рёбрам это DAG:

```text
Account[t] ──→ Strategy ──→ order_raw ──→ Risk(enforce) ──→ order_safe ──→ MarketSim ──→ Account[t+1]
```

Компилятор:

* видит, что нет SCC по instant‑рёбрам → **fixed‑point‑анализ не нужен**;
* но делает InitCheck и обнаруживает:

> **Error I001: Uninitialized read of `MarketSim.account` at t=0**
>
> Reaction `Strategy.propose` reads `AccountState` before any initialization or write.
>
> Fix: provide `init=...` for `MarketSim.account` or ensure a write happens before the first read.

Мы добавляем `init` (что уже сделали, но в примере могли забыть) — Ок.

---

# Шаг 5: Выявляем проблемы SDF / буферизации опыта

Сейчас у нас:

* `ExperienceCollector` пишет опыт **каждый тик**,
* `Trainer` делает шаг обучения **каждые N тиков** (например, раз в 100).

Допустим, пользователь решает добавить `rate`:

```python
class ExperienceCollector(CoreNode):
    price = Input[float](rate=1)
    account = Input[AccountState](rate=1)
    order = Input[OrderIntent](rate=1)
    margin_call = Input[bool](rate=1)
    out = Output[Experience](rate=1)   # 1 опыт на тик

class Trainer(ExtNode):
    exp = Input[Experience](rate=32)   # consume 32 experience per training step

    @reaction_contract(...)
    def step(self, batch: list[Experience]):
        train(batch)
```

Компилятор включает SDF‑анализ:

* `ExperienceCollector` производит 1 токен опыта per firing.
* `Trainer` потребляет 32 токена per firing.

SDF‑анализ:

* balance‑уравнения: чтобы система была consistent, надо `q_collector : q_trainer = 32 : 1`.⁵
* Если пользователь не задал такое расписание, а просто оставил их “в одном clock domain” → возможны:

  * **неограниченный рост буфера** опыта (если Trainer запускается реже, чем нужно),
  * или **голодание Trainer’а** (если наоборот).

Компилятор выдаёт:

> **Warning SDF001: Potential unbounded experience buffer**
>
> Producer `ExperienceCollector` (rate=1) and consumer `Trainer` (rate=32) form an SDF subgraph.
> A consistent schedule requires 32:1 firing ratio (collector:trainer).
> Current clock configuration does not guarantee this; the buffer may grow without bound.
>
> Suggestions:
> • make Trainer fire exactly every 32 ticks, or
> • introduce a bounded replay buffer with backpressure/drop policy.

Разработчик добавляет простой механизм:

```python
class ReplayBuffer(CoreNode):
    exp_in = Input[Experience](rate=1)
    exp_out = Output[Experience](rate=32)
    maxlen = 10_000

    @state
    def buffer(self) -> State[deque[Experience]]:
        return State(init=deque(), write_policy="bounded_fifo")

    @reaction
    def push(self, e: Expr[Experience]):
        # при переполнении дропаем старые, но буфер ограничен
        ...

    @reaction
    def pop_batch(self) -> Expr[list[Experience]]:
        # выдаём batch из 32, если есть
        ...
```

SDF‑анализ теперь:

* Видит `bounded_fifo`, явно ограниченную,
* firing‑вектора совместимы,
* ливнес/ограниченность → Ок.

---

# Шаг 6: Итог — что отловил компилятор и что мы “исправили в мозгу”

Сводно:

1. **Глобальный shared state без декларации**
   → **Error S001** (RawNode в алгебраическом цикле)
   → заставил нас оформить `AccountState` как `State` и перевести на Intents.

2. **Множественные писатели аккаунта**
   → **Error S002 (Multiple writers)**
   → вынудил нас разделить ответственность: MarketSim — единственный writer, Risk — корректирует только ордера.

3. **Неочевидный algebraic loop по ордерам**
   → **Error C003 (Non-constructive cycle)**
   → заставил осознанно выбрать семантику “один шаг risk‑enforce” и убрать мгновенный фикс‑пойнт.

4. **Неинициализированное значение аккаунта**
   → **Error I001 (Uninitialized read)**
   → напомнил задать `init` в `State`.

5. **Несогласованный SDF‑подграф с опытом**
   → **Warning SDF001 (Unbounded buffer)**
   → заставил ввести bounded replay‑буфер и задуматься о частоте обучения.

В результате мы получили:

* Явную модель состояния и времени (`State`, `Tag=(t,μ)`).¹²
* Детерминированную модель исполнения (один writer аккаунта, merge‑политики на других переменных).³
* Чёткую структуру: Core‑логика там, где важна формальная корректность, и Extended/Raw вокруг для доменных деталей и ML.⁵⁻⁷

Компилятор, по сути, **прогнал нас через правильную модель в голове**: от “просто напишу код” к модели в духе Ptolemy/Esterel/SDF с явными задержками, fixed‑пойнтами и буферами.¹²

---

## Термины (для ясности)

* **Algebraic loop (алгебраический цикл)** — зависимость значения от самого себя в одном логическом тике (без задержки). Требует фикс‑пойнт‑анализа или разрыва задержкой.²³
* **Constructive causality** — более сильное условие, чем просто “существует решение”: значения должны вычисляться конструктивно через монотонную итерацию в трёхзначной логике, без угадываний.⁵⁻⁷
* **Non‑Zeno** — отсутствие бесконечного числа микрошагов при фиксированном времени `t`.⁴
* **SDF (Synchronous Dataflow)** — модель с фиксированными rate’ами на портах; даёт статический анализ ливнеса и ограниченности буферов.⁵
* **WritePolicy** — политика объединения множественных записей в одну переменную за тик (моноид, LWW, error).³

---
