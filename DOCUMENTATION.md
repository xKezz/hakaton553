# Система автоматического расчёта скидок для маркетинговых кампаний

Проект рассчитывает клиентские метрики, строит сегментацию на основе модифицированного RFM-подхода, назначает клиентов на маркетинговые категории и автоматически подбирает скидки для кампании возврата клиентов (`win-back`).

Итоговый результат может быть выгружен в структурированный `JSON`-отчёт, содержащий:

- метаинформацию;
- список категорий;
- список клиентов;
- назначенные скидки;
- причины назначения скидки;
- предупреждения о качестве данных и настройках политики.

---

## 1. Основная идея

Для каждого клиента рассчитываются показатели:

| Показатель | Описание |
|---|---|
| `recency` | количество дней с момента последней покупки |
| `frequency` | количество покупок |
| `total_amount` | общая сумма покупок |
| `avg_amount` | средний чек |
| `monetary_score` | комбинированный нормированный показатель ценности клиента |

Далее строится матрица сегментов:

```text
3 уровня recency × 3 уровня frequency × 3 уровня monetary_score
```

Максимально возможное количество базовых групп:

```text
3 × 3 × 3 = 27
```

Затем базовые группы сортируются по приоритету и разбиваются на `count_cat` маркетинговых категорий.

Для кампании `win-back`:

- категория `0` — самая приоритетная;
- чем больше клиент не покупал и чем выше его ценность, тем выше приоритет.

---

## 2. Структура проекта

```text
.
├── business_metrics.py
├── user_categories.py
├── discount_policy.py
├── pipeline.py
├── requirements.txt
└── main.py
```

---

## 3. Требования

Проект использует:

- `Python 3.10+`;
- `pandas`;
- `numpy`.

Рекомендуемые версии указаны в `requirements.txt`:

```text
pandas>=2.0.0
numpy>=1.26.0
```

---

## 4. Установка

### 4.1. Перейти в папку проекта

```bash
cd "путь/к/проекту"
```

Например:

```bash
cd "/mnt/c/Users/ASUS/Desktop/Учеба/Hacatons/hacaton_max553/realise 1.0"
```

или в Windows:

```powershell
cd "C:\Users\ASUS\Desktop\Учеба\Hacatons\hacaton_max553\realise 1.0"
```

### 4.2. Создать виртуальное окружение

```bash
python -m venv .venv
```

или:

```bash
python3 -m venv .venv
```

### 4.3. Активировать виртуальное окружение

#### Windows

```powershell
.venv\Scripts\activate
```

#### Linux / WSL / macOS

```bash
source .venv/bin/activate
```

### 4.4. Установить зависимости

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Если файла `requirements.txt` нет, можно установить вручную:

```bash
python -m pip install pandas numpy
```

---

## 5. Формат входных данных

Входной файл должен быть подготовлен заранее.

Ожидаемые колонки:

```text
client_id,purchase_date,amount
```

Пример:

```csv
client_id,purchase_date,amount
+79991234567,2026-05-01,390.0
+79991234567,2026-06-01,450.0
+79997654321,2026-04-15,1200.0
```

### Требования к колонкам

| Колонка | Тип | Описание |
|---|---|---|
| `client_id` | строка | идентификатор клиента |
| `purchase_date` | дата | дата покупки |
| `amount` | число | сумма покупки |

Особенности:

- `client_id` читается как строка;
- это позволяет сохранять `+`, ведущие нули и другие нечисловые идентификаторы;
- `purchase_date` парсится как дата;
- `amount` приводится к числу;
- рекомендуемый формат даты: `YYYY-MM-DD`.

---

## 6. Быстрый старт

### 6.1. Генерация отчёта из CSV

```python
from pipeline import save_discount_report

save_discount_report(
    path="discount_report.json",
    path_data="clients.csv",
    count_cat=3,
    max_discount=30.0,
    step=5.0,
)
```

После выполнения в текущей папке появится файл:

```text
discount_report.json
```

---

### 6.2. Генерация отчёта с фиксированной датой отсчёта

Для воспроизводимости результатов рекомендуется передавать `reference_date`.

```python
import pandas as pd

from pipeline import save_discount_report

save_discount_report(
    path="discount_report.json",
    path_data="clients.csv",
    reference_date=pd.Timestamp("2026-06-22"),
    count_cat=3,
    max_discount=30.0,
    step=5.0,
)
```

---

### 6.3. Генерация отчёта из pandas DataFrame

```python
import pandas as pd

from pipeline import build_discount_report

df = pd.DataFrame(
    {
        "client_id": ["+79991234567", "+79997654321"],
        "purchase_date": ["2026-05-01", "2026-04-15"],
        "amount": [390.0, 1200.0],
    }
)

report = build_discount_report(
    data=df,
    reference_date=pd.Timestamp("2026-06-22"),
    count_cat=2,
)

print(report["meta"])
print(report["categories"])
print(report["clients"])
```

---

## 7. Основные модули

### 7.1. `business_metrics.py`

Модуль отвечает за загрузку данных и расчёт клиентских метрик.

Основной класс:

```python
BusinessMetrics
```

Пример:

```python
import pandas as pd

from business_metrics import BusinessMetrics

bm = BusinessMetrics(
    path_data="clients.csv",
    reference_date=pd.Timestamp("2026-06-22"),
)

metrics = bm.metrics()

print(metrics.head())
```

Результат содержит колонки:

```text
client_id
last_purchase_date
recency
frequency
total_amount
avg_amount
monetary_score
```

---

### 7.2. `user_categories.py`

Модуль строит матрицу категорий и назначает клиентов на категории.

Основной класс:

```python
UserCategories
```

Пример:

```python
from user_categories import UserCategories

uc = UserCategories(
    path_to_data="clients.csv",
    reference_date=pd.Timestamp("2026-06-22"),
    method="win-back",
)

clients_with_campaign = uc.users_cat(count_cat=3)

print(clients_with_campaign.head())
```

После вызова `users_cat()` таблица клиентов содержит колонку:

```text
campaign
```

Где:

```text
0 — самая приоритетная категория
1 — менее приоритетная
2 — ещё менее приоритетная
...
```

---

### 7.3. `discount_policy.py`

Модуль рассчитывает скидку для каждой маркетинговой категории.

Основной класс:

```python
DiscountPolicy
```

Пример:

```python
from discount_policy import DiscountPolicy

dp = DiscountPolicy(
    uc,
    max_discount=30.0,
    step=5.0,
)

clients = dp.clients()

print(clients.head())
```

Результат содержит:

```text
client_id
campaign
discount
reason
```

---

### 7.4. `pipeline.py`

Модуль объединяет все шаги и формирует итоговый `JSON`-отчёт.

Основные функции:

```python
build_discount_report()
save_discount_report()
```

---

## 8. Параметры запуска

### 8.1. Основные параметры `build_discount_report`

| Параметр | Тип | Значение по умолчанию | Описание |
|---|---:|---:|---|
| `path_data` | `str` или `None` | `None` | путь к CSV-файлу |
| `data` | `pd.DataFrame` или `None` | `None` | готовый DataFrame с данными |
| `reference_date` | `pd.Timestamp` или `None` | сегодня | дата отсчёта |
| `count_cat` | `int` | `3` | количество маркетинговых категорий |
| `method` | `str` | `"win-back"` | маркетинговая стратегия |
| `max_discount` | `float` | `30.0` | максимальная скидка в процентах |
| `step` | `float` | `5.0` | шаг скидки |
| `need_free` | `float` | `0.25` | порог, ниже которого скидка не нужна |
| `value_cut` | `float` | `0.20` | порог отсечения низкой ценности |
| `churn_discount` | `float` | `5.0` | минимальная скидка для отточного сегмента |
| `single_category_discount` | `float` или `None` | `None` | фиксированная скидка для одной категории |
| `include_client_ids` | `bool` | `True` | включать ли `client_ids` в категории |

---

## 9. Дата отсчёта и будущие даты

### 9.1. Дата отсчёта

По умолчанию используется сегодняшний день без времени:

```python
pd.Timestamp.today().normalize()
```

Для воспроизводимости рекомендуется передавать дату вручную:

```python
reference_date=pd.Timestamp("2026-06-22")
```

---

### 9.2. Будущие даты

Если дата покупки больше `reference_date`:

```text
purchase_date > reference_date
```

то такая дата обрезается до `reference_date`.

Логика:

```python
effective_purchase_date = min(purchase_date, reference_date)
last_purchase_date = max(effective_purchase_date)
recency = reference_date - last_purchase_date
recency = max(recency, 0)
```

Следовательно:

```text
recency для будущих покупок = 0
```

Если в данных найдены будущие даты, код пишет предупреждение:

```text
Найдены покупки с датой позже reference_date. Они будут обрезаны до даты отсчёта.
```

---

## 10. Клиентские метрики

Для каждого клиента рассчитываются:

```text
client_id
last_purchase_date
recency
frequency
total_amount
avg_amount
monetary_score
```

### 10.1. `recency`

Количество дней с последней покупки.

```text
recency = reference_date - last_purchase_date
```

Если последняя покупка в будущем, `recency = 0`.

---

### 10.2. `frequency`

Количество покупок клиента.

```text
frequency = count(amount)
```

---

### 10.3. `total_amount`

Сумма всех покупок клиента.

```text
total_amount = sum(amount)
```

---

### 10.4. `avg_amount`

Средний чек клиента.

```text
avg_amount = mean(amount)
```

---

### 10.5. `monetary_score`

Комбинированный показатель покупательской способности клиента.

Сначала нормируются:

```text
total_amount
avg_amount
frequency
```

Затем применяется формула:

```text
monetary_score =
    0.40 * total_norm +
    0.40 * avg_norm +
    0.20 * frequency_norm
```

Диапазон:

```text
0.0 <= monetary_score <= 1.0
```

---

## 11. Матрица категорий

Матрица строится из трёх уровней для каждого показателя:

```text
min
mean
max
```

Используются:

```text
recency
frequency
monetary_score
```

Итого:

```text
3 × 3 × 3 = 27 базовых групп
```

Если некоторые точки совпадают, дубликаты автоматически удаляются.

Например, если все клиенты одинаковые, матрица может схлопнуться до одной группы.

---

## 12. Приоритет категорий для `win-back`

Для стратегии `win-back` высокий `recency` повышает приоритет.

То есть:

```text
чем дольше клиент не покупал, тем выше приоритет возврата
```

Формула приоритета базовой группы:

```text
score =
    0.60 * recency_norm +
    0.10 * frequency_norm +
    0.30 * monetary_norm
```

Где:

```text
recency_norm = 0 — клиент недавно был
recency_norm = 1 — клиент давно не был
```

Категория `0` — самая приоритетная.

---

## 13. Автоматическое уменьшение количества категорий

Пользователь запрашивает:

```python
count_cat = N
```

Но уникальных групп может быть меньше, чем `N`.

Например:

```text
запрошено 10 категорий
уникальных групп только 7
```

Тогда код автоматически уменьшит количество категорий до `7` и напишет предупреждение.

Также:

- если `count_cat <= 0` — ошибка;
- если данных нет — возвращается пустой результат с предупреждением;
- если матрица схлопнулась в одну группу, все клиенты попадут в категорию `0`.

---

## 14. Политика скидок

### 14.1. Параметры по умолчанию

```python
max_discount = 30.0
step = 5.0
need_free = 0.25
value_cut = 0.20
churn_discount = 5.0
```

---

### 14.2. Ограничения

| Условие | Поведение |
|---|---|
| `max_discount < 0` | ошибка |
| `max_discount > 30` | разрешается, но пишется предупреждение |
| `step <= 0` | ошибка |
| `step > max_discount` | `step` уменьшается до `max_discount`, пишется предупреждение |
| `single_category_discount < 0` | ошибка |
| `single_category_discount > max_discount` | значение уменьшается до `max_discount`, пишется предупреждение |

---

### 14.3. Минимальная скидка для стимулирования

Минимальная положительная скидка равна:

```text
step
```

Пример:

```text
формула посчитала 1.2%
step = 5%
итоговая скидка = 5%
```

---

### 14.4. Если категория одна

Если по итогу сегментации получилась только одна категория, все клиенты получают одну и ту же скидку.

По умолчанию скидка рассчитывается по формуле.

Но можно задать фиксированную скидку:

```python
DiscountPolicy(
    uc,
    max_discount=30.0,
    step=5.0,
    single_category_discount=10.0,
)
```

Тогда все клиенты единственной категории получат:

```text
10.0%
```

Если:

```python
single_category_discount=None
```

используется формула.

---

## 15. Логика расчёта скидки

Для каждой категории рассчитываются медианные значения:

```text
recency
frequency
monetary_score
```

Затем они нормируются:

```text
r_norm — нормированный recency
f_norm — нормированная частота
m_norm — нормированный monetary_score
```

Далее рассчитываются:

```text
need = 0.6 * r_norm + 0.4 * (1 - f_norm)
value = 0.6 * m_norm + 0.4 * f_norm
```

### 15.1. Скидка не нужна

Если:

```text
need <= need_free
```

скидка равна:

```text
0
```

Причина:

```text
высокая естественная активность - скидка не нужна
```

---

### 15.2. Минимальная скидка последнего шанса

Если:

```text
value <= value_cut
r_norm >= 0.5
```

назначается минимальная скидка для отточного сегмента.

Фактическое значение:

```text
max(step, churn_discount)
```

но не больше:

```text
max_discount
```

Причина:

```text
низкая ценность при оттоке - минимальная скидка последнего шанса
```

---

### 15.3. Основная формула скидки

В остальных случаях:

```text
raw_discount =
    max_discount * need * (0.5 + 0.5 * value)
```

Затем скидка округляется до шага:

```text
discount = round_to_step(raw_discount, step)
```

И ограничивается диапазоном:

```text
step <= discount <= max_discount
```

Причина:

```text
стимулирование спроса
```

---

## 16. Бюджет акции

Бюджет рассчитывается от среднего чека:

```text
budget =
    sum(
        avg_amount * discount / 100 * response_rate
    )
```

Пример:

```python
budget = dp.budget(response_rate=0.1)

print(budget)
```

Где:

```text
response_rate = 0.1
```

означает ожидаемый отклик 10%.

---

## 17. Итоговый JSON

Функция:

```python
build_discount_report()
```

возвращает словарь вида:

```json
{
  "meta": {},
  "categories": [],
  "clients": []
}
```

---

### 17.1. Блок `meta`

Пример:

```json
{
  "generated_at": "2026-06-22T00:00:00",
  "reference_date": "2026-06-22",
  "method": "win-back",
  "max_discount": 30.0,
  "step": 5.0,
  "requested_count_cat": 3,
  "effective_count_cat": 3,
  "monetary_formula": "0.40*total_norm + 0.40*avg_norm + 0.20*frequency_norm",
  "score_formula": "0.60*recency_norm + 0.10*frequency_norm + 0.30*monetary_norm",
  "budget_base": "avg_amount",
  "warnings": []
}
```

---

### 17.2. Блок `categories`

Каждая категория содержит:

```json
{
  "campaign": 0,
  "label": "priority_0",
  "discount": 25.0,
  "reason": "стимулирование спроса",
  "clients_count": 1200,
  "avg_recency": 145.3,
  "avg_frequency": 2.8,
  "avg_monetary_score": 0.72,
  "avg_amount": 1450.0,
  "client_ids": [
    "+79991234567",
    "+79997654321"
  ]
}
```

Описание полей:

| Поле | Описание |
|---|---|
| `campaign` | номер категории |
| `label` | человекочитаемая метка категории |
| `discount` | скидка для категории |
| `reason` | причина назначения скидки |
| `clients_count` | количество клиентов в категории |
| `avg_recency` | средний `recency` клиентов категории |
| `avg_frequency` | средняя частота покупок |
| `avg_monetary_score` | средний показатель ценности |
| `avg_amount` | средний чек |
| `client_ids` | список идентификаторов клиентов |

Если для категории не назначено ни одного клиента, она может присутствовать с полями:

```json
{
  "clients_count": 0,
  "client_ids": []
}
```

---

### 17.3. Блок `clients`

Каждый клиент содержит:

```json
{
  "client_id": "+79991234567",
  "campaign": 0,
  "discount": 25.0,
  "reason": "стимулирование спроса",
  "recency": 52,
  "frequency": 2,
  "total_amount": 840.0,
  "avg_amount": 420.0,
  "monetary_score": 0.61
}
```

---

## 18. Предупреждения

Код использует стандартный механизм:

```python
warnings.warn()
```

Предупреждения собираются в:

```json
"meta": {
  "warnings": []
}
```

### Список возможных предупреждений

| Событие | Текст или смысл |
|---|---|
| Будущие даты | Найдены покупки позже `reference_date`, они будут обрезаны |
| Пропуски в данных | Строки с пустыми `client_id`, `purchase_date` или `amount` исключены |
| `max_discount > 30` | Скидка больше стандартного лимита |
| `step > max_discount` | Шаг уменьшен до `max_discount` |
| `count_cat` уменьшен | Запрошено больше категорий, чем доступно уникальных групп |
| `single_category_discount > max_discount` | Фиксированная скидка уменьшена до `max_discount` |
| Клиенты без скидки | Для клиентов без назначенной скидки установлена скидка `0` |

---

## 19. Ошибки

| Ситуация | Поведение |
|---|---|
| Не передан источник данных | `ValueError` |
| Отсутствуют обязательные колонки | `ValueError` |
| Некорректная дата | `ValueError` |
| Некорректный `amount` | `ValueError` |
| `count_cat <= 0` | `ValueError` |
| `step <= 0` | `ValueError` |
| `max_discount < 0` | `ValueError` |
| `single_category_discount < 0` | `ValueError` |
| `churn_discount < 0` | `ValueError` |
| Неизвестный метод | `ValueError` |
| Данные есть, но не вызван `users_cat()` перед `DiscountPolicy` | `ValueError` |

---

## 20. Пустые входные данные

Если входные данные пусты:

- код не падает молча;
- возвращается пустой результат;
- в `meta.warnings` добавляется предупреждение.

Пример:

```json
{
  "meta": {
    "warnings": [
      "Входные данные не содержат клиентов."
    ]
  },
  "categories": [],
  "clients": []
}
```

---

## 21. Пример `main.py`

```python
import pandas as pd

from pipeline import save_discount_report


def main():
    report = save_discount_report(
        path="discount_report.json",
        path_data="clients.csv",
        count_cat=3,
        max_discount=30.0,
        step=5.0,
    )

    print("Отчёт сохранён в файл: discount_report.json")
    print("Количество категорий:", len(report["categories"]))
    print("Количество клиентов:", len(report["clients"]))
    print("Предупреждения:", report["meta"]["warnings"])


if __name__ == "__main__":
    main()
```

Запуск:

```bash
python main.py
```

---

## 22. Расширение маркетинговых стратегий

Сейчас явно поддерживается стратегия:

```python
"win-back"
```

Архитектура позволяет добавлять новые стратегии.

Пример регистрации новой стратегии:

```python
from user_categories import UserCategories

UserCategories.register_strategy(
    name="upsell",
    weights=(0.20, 0.30, 0.50),
    recency_higher_is_better=False,
)
```

После этого можно использовать:

```python
uc = UserCategories(
    path_to_data="clients.csv",
    method="upsell",
)
```

Параметр:

```python
recency_higher_is_better
```

определяет, повышает ли высокий `recency` приоритет.

Для `win-back`:

```python
recency_higher_is_better=True
```

Для стратегий типа `upsell`, вероятно, потребуется:

```python
recency_higher_is_better=False
```

или отдельная более сложная логика.

---

## 23. Миграция со старых имён

Ранее файлы назывались:

```text
Bisness_metrics.py
Catigories_users.py
DiscontPolicy.py
```

Новые имена:

```text
business_metrics.py
user_categories.py
discount_policy.py
```

Старые классы:

```text
Bisness_metrics
Сatigories_users
DiscountPolicy
```

Новые классы:

```text
BusinessMetrics
UserCategories
DiscountPolicy
```

Актуальный импорт:

```python
from business_metrics import BusinessMetrics
from user_categories import UserCategories
from discount_policy import DiscountPolicy
from pipeline import build_discount_report, save_discount_report
```

---

## 24. Рекомендации по эксплуатации

1. Всегда сохраняйте исходный файл с данными.
2. Используйте фиксированный `reference_date` для воспроизводимых отчётов.
3. Проверяйте `meta.warnings` после каждого запуска.
4. При большом количестве клиентов можно отключить включение `client_ids` в категории:

```python
build_discount_report(
    path_data="clients.csv",
    include_client_ids=False,
)
```

5. Перед запуском акции проверяйте:
   - количество категорий;
   - распределение клиентов;
   - распределение скидок;
   - прогноз бюджета.

---

## 25. Итоговая схема работы

```text
1. Загрузка данных
   client_id, purchase_date, amount

2. Предобработка
   - client_id как строка
   - дата как datetime
   - amount как число
   - будущие даты клипятся до reference_date
   - пустые строки исключаются с предупреждением

3. Расчёт клиентских метрик
   - recency
   - frequency
   - total_amount
   - avg_amount
   - monetary_score

4. Построение матрицы сегментов
   - 27 базовых групп
   - удаление дубликатов
   - расчёт score
   - сортировка по приоритету

5. Назначение клиентов на группы
   - ближайшая группа по нормированным метрикам

6. Разбиение групп на маркетинговые категории
   - категория 0 — самая приоритетная
   - автоматическое уменьшение числа категорий при необходимости

7. Расчёт скидки
   - по правилам DiscountPolicy
   - с ограничениями и предупреждениями

8. Выгрузка результата
   - categories
   - clients
   - meta
```