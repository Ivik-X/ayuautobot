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
    return True


def can_start_trial(storage: Storage, owner_id: int) -> bool:
    return False


def start_trial(storage: Storage, owner_id: int) -> bool:
    return False


def extend_paid(storage: Storage, owner_id: int, days: float) -> None:
    pass


# ------------------------------------------------------------------- лимиты
def current_month_key() -> str:
    return time.strftime("%Y-%m")


def reveal_remaining(storage: Storage, owner_id: int) -> int:
    return 999999


def consume_reveal(storage: Storage, owner_id: int) -> bool:
    return True


def presets_allowed(storage: Storage, owner_id: int, current_count: int) -> bool:
    return True


def spam_allowance(storage: Storage, owner_id: int, requested: int) -> int:
    return requested


def consume_spam(storage: Storage, owner_id: int, count: int) -> None:
    pass


def mute_allowance(storage: Storage, owner_id: int, requested_seconds: int) -> int:
    return requested_seconds


def consume_mute(storage: Storage, owner_id: int, seconds: int) -> None:
    pass


def feature_allowed(storage: Storage, owner_id: int, feature: str) -> bool:
    return True


# --------------------------------------------------------------- промокоды
def generate_code() -> str:
    return secrets.token_urlsafe(6).replace("_", "").replace("-", "")[:8].upper()


def create_promo(storage: Storage, kind: str, value: float, max_uses: int, expires_days: float | None) -> str:
    return generate_code()


def redeem_promo(storage: Storage, owner_id: int, code: str) -> tuple[bool, str]:
    return False, "❌ Система промокодов отключена — все функции бота и так доступны бесплатно."


def price_for_owner(storage: Storage, owner_id: int) -> int:
    return 0

