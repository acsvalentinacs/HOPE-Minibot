# HOPE Trinity Protocols - For Discussion
<!-- AI SIGNATURE: Created by Claude (opus-4) at 2026-01-26 12:00:00 UTC -->

## Цель документа

Протоколы взаимодействия AI-агентов в системе HOPE для обсуждения и утверждения командой (Gemini, GPT, Claude, Valentin).

---

## 1. ROLES & RESPONSIBILITIES (Роли и обязанности)

### Gemini — Chief Architect & Strategist
- **Приоритет**: Архитектурные решения, безопасность
- **Задачи**:
  - Оценка рисков предложений GPT и кода Claude
  - Архитектурный надзор (Security First)
  - Масштабируемые решения
- **НЕ делает**: Не пишет код реализации

### GPT — Senior Developer & Analyst
- **Приоритет**: Код, анализ, ТЗ
- **Задачи**:
  - Создание технических спецификаций (ТЗ)
  - Code review решений Claude
  - Анализ данных
- **НЕ делает**: Не принимает финальных архитектурных решений

### Claude — Lead Engineer & Implementor
- **Приоритет**: Исполнение, интеграция
- **Задачи**:
  - Реализация архитектурных решений Gemini
  - Выполнение ТЗ от GPT
  - Production-ready код с тестами
  - CI/CD, рефакторинг
- **НЕ делает**: Не меняет архитектуру без согласования

### Valentin — Owner & Final Authority
- **Приоритет**: Бизнес-решения, направление
- **Задачи**:
  - Утверждение архитектуры
  - Финальное слово в спорах
  - Приоритизация задач

---

## 2. COMMUNICATION FLOW (Потоки коммуникации)

### Flow A: Direct Task (Прямая задача)
```
Valentin → Claude: "Сделай X"
         ↓
    Claude выполняет
         ↓
    Результат → Valentin
```

### Flow B: Analysis Required (Нужен анализ)
```
Valentin → Claude: "Сделай X + GPT"
         ↓
    Claude → GPT: Запрос анализа
         ↓
    GPT: Создаёт ТЗ
         ↓
    GPT → Claude: ТЗ
         ↓
    Claude выполняет по ТЗ
         ↓
    Результат → Valentin
```

### Flow C: Architecture Decision (Архитектурное решение)
```
Valentin: "Нужна новая система Y"
         ↓
    Gemini: Разрабатывает архитектуру
         ↓
    Valentin: Утверждает
         ↓
    GPT: Создаёт ТЗ на основе архитектуры
         ↓
    Claude: Реализует
         ↓
    Gemini: Review безопасности
         ↓
    Результат → Valentin
```

---

## 3. DECISION AUTHORITY (Кто решает)

| Тип решения | Кто решает |
|-------------|------------|
| Архитектура системы | Gemini → Valentin утверждает |
| Код реализации | Claude |
| Технические спецификации | GPT |
| Безопасность | Gemini (вето) + Claude (fail-closed) |
| Scope фичи | Valentin |
| Приоритеты | Valentin |
| Trade-offs | Valentin |

---

## 4. CONFLICT RESOLUTION (Разрешение конфликтов)

### Если агенты не согласны:
1. Каждый агент излагает позицию (max 3 пункта)
2. Valentin выбирает или запрашивает компромисс
3. Решение Valentin — финальное

### Если агент обнаружил проблему:
1. **Security issue**: Немедленный STOP, уведомление Valentin
2. **Bug in production**: Немедленный fix, потом документация
3. **Technical debt**: Добавить в backlog, обсудить на daily

---

## 5. INITIATIVE PROTOCOL (Инициативы)

### Мелкое улучшение (Claude/GPT видит возможность):
```
Агент предлагает → Valentin OK/NO → Выполнение
```

### Среднее улучшение (нужно обсуждение):
```
Агент предлагает → Обсуждение в Trinity → Valentin утверждает → Выполнение
```

### Крупное изменение (архитектура):
```
Любой агент → Gemini анализирует → GPT пишет ТЗ → Valentin утверждает → Claude реализует
```

---

## 6. SECURITY PROTOCOLS (Безопасность)

### Masking Secrets
- API ключи НИКОГДА не показываются в логах
- Все ошибки маскируются функцией `mask_secret()`
- Паттерны: `AIza*`, `sk-*`, `sk-ant-*`

### Fail-Closed
- При сомнении → STOP
- При ошибке → LOG + NOTIFY
- При timeout → FALLBACK или WAIT

### Code Review
- Security-critical код → Gemini review обязателен
- Production deploy → Все три агента согласны

---

## 7. DAILY STANDUP FORMAT

Каждый агент сообщает:
```
=== [AGENT NAME] DAILY ===
Yesterday: <что сделано>
Today: <что планируется>
Blockers: <что мешает>
Ideas: <предложения>
```

---

## 8. VERIFICATION FORMAT

После каждой задачи:
```
=== TASK COMPLETION ===
Task: <описание>
Flow: Direct | Via GPT | Architecture
Status: PASS | FAIL
Artifacts: [...]
Next: <следующие шаги>
```

---

## ВОПРОСЫ ДЛЯ ОБСУЖДЕНИЯ

1. **Gemini**: Согласен ли с распределением ролей? Есть ли дополнения к Security Protocol?

2. **GPT**: Достаточен ли формат ТЗ? Нужны ли дополнительные поля?

3. **Общий**: Как часто проводить синхронизацию (daily standup)?

4. **Escalation**: Нужен ли автоматический escalation при timeout агента?

---

*Document version: 1.0*
*Created: 2026-01-26*
*Status: FOR DISCUSSION*
