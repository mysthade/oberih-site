"""Перевірка безпечної адреси адмін-панелі (ADMIN_URL)."""
import re

from django.core.exceptions import ImproperlyConfigured

FORBIDDEN_ADMIN_URL_WORDS = (
    "admin",
    "login",
    "logon",
    "signin",
    "dashboard",
    "panel",
    "manage",
    "backend",
    "control",
    "staff",
    "cms",
    "wp-",
    "django",
)

_ALLOWED_ADMIN_PREFIX_RE = re.compile(r"^[A-Za-z0-9-]+$")


def validate_admin_url(value: str) -> str:
    """Перевіряє ADMIN_URL і повертає його або кидає ImproperlyConfigured українською."""
    cleaned = (value or "").strip()
    if not cleaned:
        raise ImproperlyConfigured(
            "Змінна середовища ADMIN_URL не задана або порожня. "
            "Вкажіть у файлі .env випадковий рядок довжиною щонайменше 16 символів із кінцевим слешем «/»."
        )
    if cleaned.startswith("/"):
        raise ImproperlyConfigured(
            "Змінна ADMIN_URL не повинна починатися зі слеша «/»."
        )
    if not cleaned.endswith("/"):
        raise ImproperlyConfigured(
            "Змінна ADMIN_URL обов'язково повинна закінчуватися слешем «/»."
        )

    prefix = cleaned[:-1]
    if not _ALLOWED_ADMIN_PREFIX_RE.fullmatch(prefix):
        raise ImproperlyConfigured(
            "Змінна ADMIN_URL може містити лише латинські літери (A-Z, a-z), цифри (0-9) та дефіс «-» "
            "(слеш «/» дозволений лише в кінці)."
        )
    if len(prefix) < 16:
        raise ImproperlyConfigured(
            "Змінна ADMIN_URL занадто коротка: частина до кінцевого слеша «/» повинна містити щонайменше 16 символів."
        )

    prefix_lower = prefix.lower()
    for word in FORBIDDEN_ADMIN_URL_WORDS:
        if word in prefix_lower:
            raise ImproperlyConfigured(
                f"Змінна ADMIN_URL містить передбачуване слово «{word}». "
                "Згенеруйте випадковий рядок без передбачуваних слів."
            )

    return cleaned

