def redact_secret(val: str) -> str:
    try:
        s = str(val)
        if len(s) <= 6:
            return "***"
        return s[:2] + "****" + s[-2:]
    except Exception:
        return "***"
