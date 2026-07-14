from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from bot.storage import Storage

TIER_FREE = "free"
TIER_TRIAL = "trial"
TIER_PAID = "paid"


def current_month_key() -> str:
    return time.strftime("%Y-%m")


@dataclass(slots=True)
class SubStatus:
    tier: str            # free / trial / paid — уже с учётом истечения срока
    trial_used: bool
    trial_until: float | None
    paid_until: float | None
    discount_percent: int | None


def get_status(storage: Storage, owner_id: int) -> SubStatus:
    row = storage.sub_get(owner_id)
    now = time.time()
    tier = row["tier"]

    if tier == "trial" and (row["trial_until"] is None or row["trial_until"] < now):
        storage.sub_downgrade_to_free(owner_id)
        tier = "free"
    elif tier == "paid" and (row["paid_until"] is None or row["paid_until"] < now):
        storage.sub_downgrade_to_free(owner_id)
        tier = "free"

    return SubStatus(
        tier=tier,
        trial_used=bool(row["trial_used"]),
        trial_until=row["trial_until"],
        paid_until=row["paid_until"],
        discount_percent=row["discount_percent"],
    )


def is_premium(storage: Storage, owner_id: int) -> bool:
    return get_status(storage, owner_id).tier in (TIER_TRIAL, TIER_PAID)


def can_start_trial(storage: Storage, owner_id: int) -> bool:
    status = get_status(storage, owner_id)
    return not status.trial_used and status.tier == TIER_FREE


def start_trial(storage: Storage, owner_id: int) -> bool:
    if not can_start_trial(storage, owner_id):
        return False
    trial_days = storage.get_global().trial_days
    storage.sub_start_trial(owner_id, trial_days)
    return True


def extend_paid(storage: Storage, owner_id: int, days: float) -> None:
    storage.sub_extend_paid(owner_id, days)


# ------------------------------------------------------------------- лимиты
def _usage(storage: Storage, owner_id: int):
    return storage.usage_get(owner_id, current_month_key())


def reveal_remaining(storage: Storage, owner_id: int) -> int:
    limit = storage.get_global().free_reveal_limit_month
    used = _usage(storage, owner_id)["reveal_count"]
    return max(0, limit - used)


def consume_reveal(storage: Storage, owner_id: int) -> bool:
    if is_premium(storage, owner_id):
        return True
    if reveal_remaining(storage, owner_id) <= 0:
        return False
    storage.usage_increment(owner_id, current_month_key(), "reveal_count", 1)
    return True


def presets_allowed(storage: Storage, owner_id: int, current_count: int) -> bool:
    if is_premium(storage, owner_id):
        return True
    return current_count < storage.get_global().free_presets_max


def spam_allowance(storage: Storage, owner_id: int, requested: int) -> int:
    """Возвращает, сколько сообщений реально можно отправить сейчас (0..requested)."""
    if is_premium(storage, owner_id):
        return requested
    limit = storage.get_global().free_spam_messages_month
    used = _usage(storage, owner_id)["spam_count"]
    remaining = max(0, limit - used)
    return min(requested, remaining)


def consume_spam(storage: Storage, owner_id: int, count: int) -> None:
    if is_premium(storage, owner_id) or count <= 0:
        return
    storage.usage_increment(owner_id, current_month_key(), "spam_count", count)


def mute_allowance(storage: Storage, owner_id: int, requested_seconds: int) -> int:
    if is_premium(storage, owner_id):
        return requested_seconds
    limit = storage.get_global().free_mute_seconds_month
    used = _usage(storage, owner_id)["mute_seconds"]
    remaining = max(0, limit - used)
    return min(requested_seconds, remaining)


def consume_mute(storage: Storage, owner_id: int, seconds: int) -> None:
    if is_premium(storage, owner_id) or seconds <= 0:
        return
    storage.usage_increment(owner_id, current_month_key(), "mute_seconds", seconds)


_FEATURE_FLAGS = {
    "view": "free_view_enabled",
    "afk": "free_afk_enabled",
    "antisearch": "free_antisearch_enabled",
    "stt": "free_stt_enabled",
    "ghost": "free_ghost_enabled",
    "extra": "free_extra_features_enabled",  # анонимные стикеры, .watch, экспорт, последние сообщения
}


def feature_allowed(storage: Storage, owner_id: int, feature: str) -> bool:
    if is_premium(storage, owner_id):
        return True
    flag = _FEATURE_FLAGS.get(feature)
    if flag is None:
        return False  # неизвестная фича — по умолчанию недоступна на бесплатном
    return bool(getattr(storage.get_global(), flag))


# --------------------------------------------------------------- промокоды
def generate_code() -> str:
    return secrets.token_urlsafe(6).replace("_", "").replace("-", "")[:8].upper()


def create_promo(storage: Storage, kind: str, value: float, max_uses: int, expires_days: float | None) -> str:
    code = generate_code()
    expires_at = time.time() + expires_days * 86400 if expires_days else None
    storage.promo_create(code, kind, value, max_uses, expires_at)
    return code


def redeem_promo(storage: Storage, owner_id: int, code: str) -> tuple[bool, str]:
    row = storage.promo_get(code.strip())
    if row is None:
        return False, "❌ Промокод не найден."
    if row["expires_at"] and row["expires_at"] < time.time():
        return False, "❌ Промокод истёк."
    if row["used_count"] >= row["max_uses"]:
        return False, "❌ Промокод исчерпан."
    if storage.promo_already_used(row["code"], owner_id):
        return False, "❌ Вы уже использовали этот промокод."

    if row["kind"] == "free_days":
        extend_paid(storage, owner_id, row["value"])
        storage.promo_mark_used(row["code"], owner_id)
        return True, f"✅ Начислено {row['value']:g} дн. полного доступа."

    if row["kind"] == "discount":
        storage.sub_set_discount(owner_id, int(row["value"]))
        storage.promo_mark_used(row["code"], owner_id)
        return True, f"✅ Скидка {row['value']:g}% будет применена при следующей оплате."

    return False, "❌ Неизвестный тип промокода."


def price_for_owner(storage: Storage, owner_id: int) -> int:
    base = storage.get_global().price_stars_per_month
    status = get_status(storage, owner_id)
    if status.discount_percent:
        return max(1, round(base * (100 - status.discount_percent) / 100))
    return base
