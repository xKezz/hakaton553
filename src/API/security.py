# src/API/security.py
import hmac
import hashlib
import time
import json
from urllib.parse import parse_qsl
from typing import Optional, Dict, Any


def verify_max_init_data(init_data: str, bot_token: str) -> Optional[Dict[str, Any]]:
    """
    Проверяет подпись initData от MAX.
    Возвращает распарсенные параметры при успехе, иначе None.
    """
    try:
        params = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = params.pop("hash", None)
        if not received_hash:
            return None

        # Собираем строку проверки: параметры (кроме hash) по алфавиту через \n
        data_check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(params.items())
        )

        # secret_key = HMAC-SHA256(key="WebAppData", data=bot_token)
        secret_key = hmac.new(
            b"WebAppData", bot_token.encode(), hashlib.sha256
        ).digest()

        # Подпись = HMAC-SHA256(key=secret_key, data=data_check_string)
        calculated_hash = hmac.new(
            secret_key, data_check_string.encode(), hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            return None

        # Защита от replay-атак: initData не старше 1 часа
        auth_date = int(params.get("auth_date", 0))
        if time.time() - auth_date > 3600:
            return None

        return params

    except Exception:
        return None


def extract_user_id(params: Dict[str, Any]) -> Optional[int]:
    """
    Достаёт user.id из распарсенных параметров initData.
    """
    try:
        user = json.loads(params.get("user", "{}"))
        return user.get("id")
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None