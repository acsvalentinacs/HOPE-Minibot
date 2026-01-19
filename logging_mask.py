import logging
import re

# Ищем Telegram токены в URL
_TOKEN_URL_RE = re.compile(
    r'(https://api\.telegram\.org/bot)(\d+:[A-Za-z0-9_\-]+)'
)

class TelegramTokenMaskFilter(logging.Filter):
    """
    Фильтр логирования, который маскирует Telegram Bot Token в URL.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True

        # Заменяем токен на ***MASKED***
        masked = _TOKEN_URL_RE.sub(r"\1***MASKED***", msg)

        if masked != msg:
            record.msg = masked
            record.args = ()

        return True

def install_httpx_token_filter(level: int | None = None) -> None:
    """
    Вешает фильтр на логгер 'httpx'.
    """
    logger = logging.getLogger("httpx")
    logger.addFilter(TelegramTokenMaskFilter())
    if level is not None:
        logger.setLevel(level)
