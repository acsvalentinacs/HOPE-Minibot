# Cleanup List - OMNI-CHAT v1.5

## Временные файлы для удаления

| Файл | Причина | Действие |
|------|---------|----------|
| `claude_full.json` | Временный файл | DELETE |
| `claude_inbox.json` | Временный файл | DELETE |
| `claude_resp.json` | Временный файл | DELETE |
| `gpt_inbox.json` | Временный файл | DELETE |
| `inbox_all.json` | Временный файл | DELETE |
| `inbox_check.json` | Временный файл | DELETE |
| `test_ddo.py` | Тестовый скрипт | DELETE |
| `core/omnichat/` | Дубликат папки | DELETE |

## Добавить в .gitignore

```
# Temporary JSON files
*_inbox.json
*_resp.json
*_full.json
inbox_*.json

# DDO results (local only)
omnichat/ddo_results/

# Test files
test_*.py
```

## Файлы для коммита

| Файл | Описание |
|------|----------|
| `omnichat/src/ddo/persistence.py` | DDO результаты persistence |
| `omnichat/src/connectors.py` | Warning suppression |

## Скрипты (оставить, но не коммитить)

- `scripts/Kill-OrphanedCmd.ps1` - полезен локально
- `scripts/install_tunnel_autostart.cmd` - локальный autostart
- `scripts/start_omnichat.cmd` - локальный запуск
- `omnichat/run_chat.bat` - локальный запуск
- `config/task_scheduler_tunnel.xml` - локальный конфиг

---
Generated: 2026-01-27
