# FIX SSH ACCESS TO VPS
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-02-05T10:00:00Z
# === END SIGNATURE ===

## ПРОБЛЕМА
SSH ключ с Windows не авторизован на VPS (Permission denied).

## РЕШЕНИЕ

### Вариант 1: Через пароль (если есть доступ)
```bash
# С Windows PowerShell:
ssh hope@46.62.232.161
# Ввести пароль когда спросит

# На VPS выполнить:
mkdir -p ~/.ssh
chmod 700 ~/.ssh
echo "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGZN1yd3jHBigsAAvYxuvyvWLBiBcUDpgHzcC48hpnDM kirilldev@kirillDevPC" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys

# Проверить:
cat ~/.ssh/authorized_keys
```

### Вариант 2: Через веб-панель хостера
1. Войти в панель управления VPS
2. Найти раздел SSH Keys / Access / Security
3. Добавить ключ:
```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGZN1yd3jHBigsAAvYxuvyvWLBiBcUDpgHzcC48hpnDM kirilldev@kirillDevPC
```

### Вариант 3: Через консоль хостера (VNC/KVM)
Если есть доступ к консоли VPS в панели хостера, выполнить команды из Варианта 1.

## ПОСЛЕ ДОБАВЛЕНИЯ КЛЮЧА - ПРОВЕРКА
```powershell
# С Windows:
ssh -i ~/.ssh/id_ed25519_hope hope@46.62.232.161 "echo 'SSH OK'"
```

## ДАЛЕЕ - ДЕПЛОЙ
После успешного SSH выполнить:
```bash
cd /opt/hope/minibot
git pull origin master
sudo systemctl restart hope-core
sudo systemctl restart hope-autotrader
curl http://localhost:8201/health | jq '.ai_gate'
```
