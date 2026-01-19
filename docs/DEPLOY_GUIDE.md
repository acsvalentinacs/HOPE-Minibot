# HOPE Minibot Deploy Guide

## Архитектура: Windows ↔ VPS

```
Windows (IDE, разработка)         VPS 46.62.232.161 (24/7)
-------------------------         -----------------------
minibot/ (код)                   /opt/hope/minibot/ (прод)
  |                                   |
  | git push → GitHub                 | git pull
  v                                   v
GitHub private repo  ←────────────→  Deploy + systemctl restart
  |
  | SSH tunnel                        |
  +────────────────────────────────→ friend-bridge :8765 (localhost)
                                      | IPC: /opt/hope/minibot/ipc/*
                                      | gpt-bridge-runner 24/7
```

## Первоначальная настройка

### 1. GitHub Private Repo

1. Создай private repo на GitHub: `hope-minibot`
2. На Windows добавь remote:
   ```cmd
   cd C:\Users\kirillDev\Desktop\TradingBot\minibot
   git remote add origin git@github.com:YOUR_USERNAME/hope-minibot.git
   git branch -M main
   git push -u origin main
   ```

### 2. VPS Deploy Key

На VPS:
```bash
# Генерируем SSH ключ для GitHub
ssh-keygen -t ed25519 -C "hope-vps-deploy" -f ~/.ssh/id_ed25519_github -N ""

# Показать публичный ключ
cat ~/.ssh/id_ed25519_github.pub
# ↑ Добавить этот ключ в GitHub → Settings → Deploy Keys

# SSH config для GitHub
cat >> ~/.ssh/config << 'EOF'
Host github.com
    IdentityFile ~/.ssh/id_ed25519_github
    IdentitiesOnly yes
EOF
```

### 3. Первый clone на VPS

```bash
cd /opt/hope

# Backup старой папки (если есть)
mv minibot minibot_old_$(date +%Y%m%d) 2>/dev/null || true

# Clone
git clone git@github.com:YOUR_USERNAME/hope-minibot.git minibot

# Восстановить state и .env (НЕ коммитятся)
cp /opt/hope/minibot_old_*/state/*.txt /opt/hope/minibot/state/ 2>/dev/null || true
cp /opt/hope/minibot_old_*/.env /opt/hope/minibot/ 2>/dev/null || true

# Создать ipc папки
mkdir -p /opt/hope/minibot/ipc/{gpt_inbox,claude_inbox}
chmod 700 /opt/hope/minibot/ipc

# Restart services
systemctl restart friend-bridge gpt-bridge-runner
```

---

## Ежедневный workflow

### Windows → VPS deploy

```cmd
REM 1. Commit changes
cd C:\Users\kirillDev\Desktop\TradingBot\minibot
git add .
git commit -m "Fix: description"
git push

REM 2. Deploy на VPS (через SSH)
ssh -i C:\Users\kirillDev\.ssh\id_ed25519_hope root@46.62.232.161 "cd /opt/hope/minibot && ./scripts/vps_pull_deploy.sh"
```

### MacBook → VPS deploy (когда Windows выключен)

```bash
# SSH на VPS
ssh -i ~/.ssh/id_ed25519_hope root@46.62.232.161

# На VPS
cd /opt/hope/minibot && ./scripts/vps_pull_deploy.sh
```

Или одной командой:
```bash
ssh -i ~/.ssh/id_ed25519_hope root@46.62.232.161 "cd /opt/hope/minibot && ./scripts/vps_pull_deploy.sh"
```

---

## SSH Key для MacBook Air

### Копирование ключа с Windows на MacBook

1. На Windows найди файл:
   ```
   C:\Users\kirillDev\.ssh\id_ed25519_hope
   C:\Users\kirillDev\.ssh\id_ed25519_hope.pub
   ```

2. Перенеси оба файла на MacBook в `~/.ssh/`:
   - Через USB-флешку
   - Через iCloud/Dropbox (временно)
   - Через scp с другого устройства

3. На MacBook:
   ```bash
   chmod 600 ~/.ssh/id_ed25519_hope
   chmod 644 ~/.ssh/id_ed25519_hope.pub
   ```

4. Проверь подключение:
   ```bash
   ssh -i ~/.ssh/id_ed25519_hope root@46.62.232.161 "echo OK"
   ```

### Туннель с MacBook

```bash
# В отдельном терминале (держать открытым)
ssh -i ~/.ssh/id_ed25519_hope -N -L 18765:127.0.0.1:8765 root@46.62.232.161

# В другом терминале - проверка
curl -s http://127.0.0.1:18765/healthz
```

---

## E2E Проверка

### E2E-1: Транспортный цикл

```bash
# 1. Туннель открыт
curl -s http://127.0.0.1:18765/healthz
# Ожидание: {"ok": true, ...}

# 2. Отправить сообщение GPT
# Windows:
C:\Users\kirillDev\Desktop\TradingBot\minibot\scripts\send_to_gpt.cmd "Test message"

# 3. На VPS проверить обработку
journalctl -u gpt-bridge-runner --since "2 min ago" | grep -E "(Processing|Sent|Cursor)"

# 4. Проверить inbox Claude
curl -s -H "X-HOPE-Token: YOUR_TOKEN" "http://127.0.0.1:18765/inbox/claude?limit=3"
```

### E2E-2: Контрактный цикл (task/result)

```json
// task_request (отправляет Claude)
{
  "to": "gpt",
  "type": "task_request",
  "payload": {
    "context": "friend_chat",
    "message": "Give me a task"
  }
}

// task (возвращает GPT)
{
  "type": "task",
  "payload": {
    "correlation_id": "uuid-xxx",
    "description": "...",
    "acceptance_criteria": ["..."],
    "verification_commands": ["..."]
  }
}

// task_result (отправляет Claude)
{
  "to": "gpt",
  "type": "task_result",
  "payload": {
    "correlation_id": "uuid-xxx",
    "outcome": "pass",
    "changed_files": [...],
    "commands_run": [{"cmd": "...", "exit_code": 0}]
  }
}
```

---

## Troubleshooting

### Git "worktree" error

```cmd
cd C:\Users\kirillDev\Desktop\TradingBot\minibot
rmdir /s /q .git
git init
git add .
git commit -m "Re-init"
```

### Сервисы не запускаются на VPS

```bash
# Логи
journalctl -u friend-bridge -e
journalctl -u gpt-bridge-runner -e

# Проверка .env
cat /opt/hope/minibot/.env | grep -E "^(OPENAI|FRIEND)"

# Ручной запуск для debug
cd /opt/hope/minibot
python3 -m core.friend_bridge_server --insecure
```

### MacBook не подключается к VPS

```bash
# Verbose SSH
ssh -v -i ~/.ssh/id_ed25519_hope root@46.62.232.161

# Проверка ключа
ssh-keygen -l -f ~/.ssh/id_ed25519_hope
```
