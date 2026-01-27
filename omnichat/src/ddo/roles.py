# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T12:35:00Z
# Purpose: DDO Role definitions and prompt templates
# === END SIGNATURE ===
"""
DDO Roles and Prompt Templates.

Defines role-specific prompts for each agent at each phase.
Prompts are designed to elicit structured, verifiable responses.

Design Principles:
1. Each prompt has clear output format requirements
2. Agents must provide evidence/reasoning, not just conclusions
3. All prompts request explicit confidence levels
4. Security and quality markers are required where applicable
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .types import DiscussionPhase, DiscussionContext


@dataclass
class RoleConfig:
    """Configuration for an agent's role in a phase."""
    agent: str
    phase: DiscussionPhase
    timeout_seconds: int = 90


# === PROMPT TEMPLATES ===
# Variables: {topic}, {goal}, {constraints}, {discussion_id}
# Previous responses: {architect_response}, {analyze_response}, etc.

PROMPTS = {
    # ==================== ARCHITECT PHASE ====================
    DiscussionPhase.ARCHITECT: """
## 🏗️ ARCHITECT PHASE - Architectural Design

### Контекст
**Тема дискуссии:** {topic}
**Цель:** {goal}
**Ограничения:** {constraints}
**Discussion ID:** {discussion_id}

### Твоя роль
Ты — Главный Архитектор. Предложи 2-3 архитектурных решения для данной задачи.
Для каждого варианта оцени плюсы, минусы и риски безопасности.

### ⚠️ Формат ответа (СТРОГО ОБЯЗАТЕЛЕН)

```
### Вариант 1: [Название решения]

**Описание:**
[2-3 предложения описания подхода]

**Архитектура:**
[Компоненты, их взаимодействие]

**Плюсы:**
- [плюс 1]
- [плюс 2]

**Минусы:**
- [минус 1]
- [минус 2]

**Риски безопасности:**
- [риск 1 + mitigation]

**Сложность:** Low | Medium | High
**Масштабируемость:** Low | Medium | High

---

### Вариант 2: [Название]
[аналогичная структура]

---

### 📌 Моя рекомендация

Я рекомендую **Вариант X** по следующим причинам:
1. [причина 1]
2. [причина 2]

**Уверенность в рекомендации:** [0-100]%

### ❓ Вопросы для уточнения (если есть)
- [вопрос 1]
```

### Требования
- Минимум 2 варианта, максимум 4
- Каждый вариант должен быть реализуемым
- Указывай конкретные технологии/паттерны
- Рекомендация обязательна
""",

    # ==================== ANALYZE PHASE ====================
    DiscussionPhase.ANALYZE: """
## 📊 ANALYZE PHASE - Technical Analysis & Specification

### Контекст
**Тема:** {topic}
**Цель:** {goal}

### Предложения архитектора (Gemini):
{architect_response}

### Твоя роль
Ты — Senior Analyst. Проанализируй предложенные варианты и создай ТЗ для реализации.

### ⚠️ Формат ответа (СТРОГО ОБЯЗАТЕЛЕН)

```
## Анализ архитектурных вариантов

### Сравнительная таблица

| Критерий | Вариант 1 | Вариант 2 | Вариант 3 |
|----------|-----------|-----------|-----------|
| Сложность реализации | Low/Med/High | ... | ... |
| Время разработки | ... | ... | ... |
| Масштабируемость | ... | ... | ... |
| Поддерживаемость | ... | ... | ... |
| Тестируемость | ... | ... | ... |
| Риски | ... | ... | ... |

### 📌 Выбор: Вариант X

**Обоснование выбора:**
1. [причина 1]
2. [причина 2]
3. [причина 3]

**Согласие с архитектором:** Да / Нет / Частично
[если нет — объяснение]

---

## 📋 Техническое Задание

### Цель
[Чёткая формулировка что должно быть сделано]

### Scope
**В scope:**
- [что входит 1]
- [что входит 2]

**Вне scope:**
- [что НЕ входит]

### Функциональные требования
1. [FR-1]: [описание]
2. [FR-2]: [описание]

### Нефункциональные требования
1. [NFR-1]: [производительность/безопасность/etc]

### API/Интерфейс
```python
# Сигнатуры функций/классов
```

### Критерии приёмки
- [ ] [критерий 1]
- [ ] [критерий 2]
- [ ] [критерий 3]

### Зависимости
- [зависимость 1]

**Уверенность в ТЗ:** [0-100]%
```
""",

    # ==================== IMPLEMENT PHASE ====================
    DiscussionPhase.IMPLEMENT: """
## 💻 IMPLEMENT PHASE - Code Implementation

### Контекст
**Тема:** {topic}
**Цель:** {goal}
**Discussion ID:** {discussion_id}

### Архитектура (Gemini):
{architect_response}

### ТЗ (GPT):
{analyze_response}

### Твоя роль
Ты — Lead Engineer. Напиши production-ready код согласно ТЗ.

### ⚠️ Требования к коду (ОБЯЗАТЕЛЬНО)
1. Python 3.11+
2. Type hints для ВСЕХ функций и методов
3. Docstrings для ВСЕХ public методов
4. Обработка ошибок (fail-closed)
5. Логирование через logging module
6. Никаких TODO/FIXME/placeholder в production коде
7. Никаких hardcoded secrets

### ⚠️ Формат ответа (СТРОГО ОБЯЗАТЕЛЕН)

```
## Реализация

### Обзор
[1-2 предложения о том, что реализовано]

### Код

```python
# === AI SIGNATURE ===
# Generated by: DDO (Claude)
# Discussion ID: {discussion_id}
# Phase: IMPLEMENT
# === END SIGNATURE ===

\"\"\"
[Module docstring - что делает этот модуль]
\"\"\"

from __future__ import annotations

import logging
from typing import ...
from dataclasses import dataclass

_log = logging.getLogger(__name__)


[Полный рабочий код здесь]
```

### Зависимости (requirements.txt)
```
[package==version если нужны новые]
```

### Пример использования
```python
[Пример как использовать реализованный код]
```

### Тесты (опционально)
```python
[Unit tests если уместно]
```

### Известные ограничения
- [ограничение 1]
- [ограничение 2]

**Уверенность в реализации:** [0-100]%
**Покрытие ТЗ:** [0-100]%
```
""",

    # ==================== SECURITY REVIEW PHASE ====================
    DiscussionPhase.SECURITY_REVIEW: """
## 🔒 SECURITY REVIEW PHASE - Security Audit

### Контекст
**Тема:** {topic}

### Код для ревью (Claude):
{implement_response}

### Твоя роль
Ты — Security Architect. Проведи аудит безопасности кода.

### ⚠️ Чеклист проверки (ОБЯЗАТЕЛЬНО проверить ВСЁ)
- [ ] **Injection**: SQL, Command, XSS, LDAP, XML, Path Traversal
- [ ] **Auth**: Authentication bypass, Session management, Token handling
- [ ] **Data**: Input validation, Output encoding, Sensitive data exposure
- [ ] **Errors**: Information leakage in errors, Stack traces
- [ ] **Secrets**: Hardcoded credentials, API keys in code
- [ ] **Logging**: Sensitive data in logs, Log injection
- [ ] **Dependencies**: Known vulnerabilities (CVEs)
- [ ] **Crypto**: Weak algorithms, Improper key management

### ⚠️ Формат ответа (СТРОГО ОБЯЗАТЕЛЕН)

```
## 🔒 Security Audit Report

**Discussion ID:** {discussion_id}
**Auditor:** Gemini (Security Architect)
**Date:** [current date]

### Summary

| Severity | Count |
|----------|-------|
| 🔴 Critical | X |
| 🟠 High | X |
| 🟡 Medium | X |
| 🟢 Low | X |
| ℹ️ Info | X |

---

### Findings

#### [CRITICAL/HIGH/MEDIUM/LOW] Finding 1: [Название уязвимости]

**CWE:** CWE-XXX (если применимо)
**Строка:** XX-YY
**Описание:** [Что за проблема]
**Impact:** [Что может произойти]
**Proof of Concept:** (если применимо)
```
[код демонстрирующий проблему]
```

**Рекомендация:**
```python
[Исправленный код]
```

---

#### [SEVERITY] Finding 2: ...
[аналогичная структура]

---

### Чеклист

- [x] Injection attacks: [PASS/FAIL - детали]
- [x] Authentication: [PASS/FAIL/N/A]
- [x] Data validation: [PASS/FAIL]
- [x] Error handling: [PASS/FAIL]
- [x] Secrets management: [PASS/FAIL]
- [x] Logging security: [PASS/FAIL]
- [x] Dependencies: [PASS/FAIL/N/A]

### Verdict

- [ ] ✅ **APPROVED** - Код безопасен для production
- [ ] ⚠️ **APPROVED WITH CONDITIONS** - Требуются исправления перед деплоем:
  - [условие 1]
  - [условие 2]
- [ ] ❌ **REJECTED** - Критические проблемы, блокер для релиза

**Уверенность в оценке:** [0-100]%
```
""",

    # ==================== CODE REVIEW PHASE ====================
    DiscussionPhase.CODE_REVIEW: """
## 🔍 CODE REVIEW PHASE - Quality Review

### Контекст
**Тема:** {topic}

### ТЗ (GPT):
{analyze_response}

### Код (Claude):
{implement_response}

### Security Audit (Gemini):
{security_response}

### Твоя роль
Ты — Senior Code Reviewer. Проведи code review на качество и соответствие ТЗ.

### ⚠️ Чеклист (ОБЯЗАТЕЛЬНО)
- [ ] Соответствие ТЗ (все требования выполнены?)
- [ ] Code style (PEP8, naming conventions)
- [ ] Type hints (все ли есть?)
- [ ] Error handling (правильно ли обрабатываются ошибки?)
- [ ] Edge cases (обработаны ли граничные случаи?)
- [ ] Performance (нет ли очевидных проблем?)
- [ ] Testability (можно ли протестировать?)
- [ ] Documentation (docstrings, комментарии где нужно)

### ⚠️ Формат ответа (СТРОГО ОБЯЗАТЕЛЕН)

```
## 🔍 Code Review Report

**Reviewer:** GPT (Senior Developer)
**Discussion ID:** {discussion_id}

### Quality Score: X/10

### Соответствие ТЗ

| Требование | Статус | Комментарий |
|------------|--------|-------------|
| [FR-1] | ✅/❌/⚠️ | [комментарий] |
| [FR-2] | ... | ... |

**Покрытие ТЗ:** [X]%

---

### Issues

#### 🔴 [MUST FIX] Issue 1: [Название]

**Строка:** XX
**Категория:** [Bug/Style/Performance/Security]
**Описание:** [Что не так]
**Fix:**
```python
[Исправленный код]
```

---

#### 🟡 [SHOULD FIX] Issue 2: ...

---

#### 🟢 [NICE TO HAVE] Issue 3: ...

---

### Положительные моменты
- [что сделано хорошо 1]
- [что сделано хорошо 2]

### Рекомендации по улучшению
- [рекомендация 1]
- [рекомендация 2]

### Verdict

- [ ] ✅ **APPROVED** - Код готов к merge
- [ ] ⚠️ **APPROVED WITH CHANGES** - Merge после исправлений:
  - [ ] [исправление 1]
  - [ ] [исправление 2]
- [ ] ❌ **REQUEST CHANGES** - Требуется переработка

**Уверенность в оценке:** [0-100]%
```
""",

    # ==================== REFINE PHASE ====================
    DiscussionPhase.REFINE: """
## ✨ REFINE PHASE - Code Refinement

### Контекст
**Тема:** {topic}
**Discussion ID:** {discussion_id}

### Твой предыдущий код:
{implement_response}

### Security Review (Gemini):
{security_response}

### Code Review (GPT):
{code_review_response}

### Твоя роль
Ты — Lead Engineer. Внеси ВСЕ исправления из Security Review и Code Review.

### ⚠️ Требования
1. Исправить ВСЕ [MUST FIX] issues
2. Исправить ВСЕ [SHOULD FIX] issues
3. По возможности исправить [NICE TO HAVE]
4. Если что-то НЕ исправлено — объяснить почему

### ⚠️ Формат ответа (СТРОГО ОБЯЗАТЕЛЕН)

```
## Внесённые изменения

### Security Fixes
| Finding | Статус | Комментарий |
|---------|--------|-------------|
| [Finding 1] | ✅ Fixed | [что сделано] |
| [Finding 2] | ✅ Fixed | ... |
| [Finding 3] | ⏭️ Skipped | [почему] |

### Code Review Fixes
| Issue | Статус | Комментарий |
|-------|--------|-------------|
| [Issue 1] | ✅ Fixed | [что сделано] |
| [Issue 2] | ✅ Fixed | ... |

### Финальный код

```python
# === AI SIGNATURE ===
# Generated by: DDO (Claude)
# Discussion ID: {discussion_id}
# Phase: REFINE
# Version: 2.0 (after review)
# === END SIGNATURE ===

[Полный исправленный код — не diff, а весь код целиком]
```

### Что НЕ исправлено и почему
- [пункт]: [причина почему не исправлено]

### Запрос на повторное ревью
- [ ] Да, требуется повторный Security Review
- [ ] Да, требуется повторный Code Review
- [ ] Нет, все критические замечания исправлены

**Уверенность в исправлениях:** [0-100]%
```
""",

    # ==================== SYNTHESIZE PHASE ====================
    DiscussionPhase.SYNTHESIZE: """
## 📝 SYNTHESIZE PHASE - Final Result

### Дискуссия завершена

**Тема:** {topic}
**Цель:** {goal}
**Discussion ID:** {discussion_id}

### Все ответы дискуссии:
{all_responses}

### Твоя роль
Синтезируй финальный результат дискуссии в единый документ.

### ⚠️ Формат ответа (СТРОГО ОБЯЗАТЕЛЕН)

```
# 📋 Результат дискуссии DDO

**ID:** {discussion_id}
**Тема:** {topic}
**Цель:** {goal}
**Режим:** {mode}

---

## 🎯 Executive Summary

[2-3 предложения: что было сделано, какое решение принято]

---

## 🏗️ Архитектурное решение

**Выбранный подход:** [название]

**Ключевые компоненты:**
- [компонент 1]
- [компонент 2]

**Обоснование выбора:**
[почему выбрано именно это решение]

---

## 💻 Финальный код

```python
[Финальная версия кода после всех ревью]
```

---

## 📖 Документация

### Установка
```bash
[команды установки]
```

### Использование
```python
[примеры использования]
```

### API Reference
[описание API если есть]

---

## ✅ Статус проверок

| Проверка | Статус | Примечание |
|----------|--------|------------|
| Security Review | ✅ APPROVED / ⚠️ / ❌ | [детали] |
| Code Review | ✅ APPROVED / ⚠️ / ❌ | [детали] |
| Соответствие ТЗ | [X]% | [детали] |

---

## ⚠️ Известные ограничения

- [ограничение 1]
- [ограничение 2]

---

## 📊 Метрики дискуссии

- **Участники:** Gemini, GPT, Claude
- **Фаз пройдено:** [X]
- **Сообщений:** [X]
- **Время:** [MM:SS]
- **Стоимость:** $[X.XXXX]

---

## 🤝 Консенсус

**Достигнут:** Да / Нет / Частично

[Если нет — что осталось несогласованным]

---

**Сгенерировано:** DDO v1.0
**Дата:** [timestamp]
```
""",
}


# === PHASE-AGENT MAPPING ===

PHASE_AGENTS: dict[DiscussionPhase, str] = {
    DiscussionPhase.ARCHITECT: "gemini",
    DiscussionPhase.ANALYZE: "gpt",
    DiscussionPhase.IMPLEMENT: "claude",
    DiscussionPhase.SECURITY_REVIEW: "gemini",
    DiscussionPhase.CODE_REVIEW: "gpt",
    DiscussionPhase.REFINE: "claude",
    DiscussionPhase.SYNTHESIZE: "gpt",
}


def get_agent_for_phase(phase: DiscussionPhase) -> str:
    """Get which agent handles a phase."""
    return PHASE_AGENTS.get(phase, "gpt")


def get_prompt_template(phase: DiscussionPhase) -> str:
    """Get prompt template for a phase."""
    return PROMPTS.get(phase, "")


def build_prompt(
    phase: DiscussionPhase,
    context: DiscussionContext,
) -> str:
    """
    Build complete prompt for a phase using context.

    Substitutes all variables:
    - {topic}, {goal}, {constraints}, {discussion_id}, {mode}
    - {architect_response}, {analyze_response}, etc.
    - {all_responses} for synthesis

    Args:
        phase: Current phase
        context: Discussion context

    Returns:
        Complete prompt string ready to send
    """
    template = get_prompt_template(phase)
    if not template:
        raise ValueError(f"No prompt template for phase: {phase}")

    # Build substitution dict
    subs = {
        "topic": context.topic,
        "goal": context.goal,
        "constraints": ", ".join(context.constraints) if context.constraints else "нет ограничений",
        "discussion_id": context.id,
        "mode": context.mode.value,
    }

    # Add previous phase responses
    phase_response_map = {
        DiscussionPhase.ARCHITECT: "architect_response",
        DiscussionPhase.ANALYZE: "analyze_response",
        DiscussionPhase.IMPLEMENT: "implement_response",
        DiscussionPhase.SECURITY_REVIEW: "security_response",
        DiscussionPhase.CODE_REVIEW: "code_review_response",
        DiscussionPhase.REFINE: "refine_response",
    }

    for resp in context.responses:
        key = phase_response_map.get(resp.phase)
        if key:
            subs[key] = resp.content

    # Build all_responses for synthesis
    if phase == DiscussionPhase.SYNTHESIZE:
        all_parts = []
        for resp in context.responses:
            header = f"### {resp.agent.upper()} ({resp.phase.display_name})"
            all_parts.append(f"{header}\n\n{resp.content}")
        subs["all_responses"] = "\n\n---\n\n".join(all_parts)

    # Safe substitution - don't fail on missing keys
    def replace_var(match):
        key = match.group(1)
        return subs.get(key, f"[{key} not available]")

    result = re.sub(r'\{(\w+)\}', replace_var, template)

    return result.strip()
