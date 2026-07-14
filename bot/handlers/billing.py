from __future__ import annotations

import logging
import time

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from bot import subscription
from bot.storage import Storage

logger = logging.getLogger(__name__)

router = Router(name="billing")

STARS_CURRENCY = "XTR"

# user_id -> ждём текст промокода
_promo_pending: set[int] = set()


def _status_text(storage: Storage, owner_id: int) -> str:
    status = subscription.get_status(storage, owner_id)
    price = subscription.price_for_owner(storage, owner_id)

    lines = ["<b>💫 Подписка</b>\n"]
    if status.tier == subscription.TIER_PAID:
        until = time.strftime("%d.%m.%Y", time.localtime(status.paid_until))
        lines.append(f"Статус: <b>полный доступ</b> до {until}")
    elif status.tier == subscription.TIER_TRIAL:
        until = time.strftime("%d.%m.%Y", time.localtime(status.trial_until))
        lines.append(f"Статус: <b>пробный период</b> до {until}")
    else:
        lines.append("Статус: <b>бесплатный тариф</b>")

    if status.discount_percent:
        lines.append(f"У вас есть скидка {status.discount_percent}% на следующую оплату.")

    lines.append(f"\nЦена полного доступа: <b>{price} ⭐</b> / месяц")
    return "\n".join(lines)


def _status_keyboard(storage: Storage, owner_id: int) -> InlineKeyboardMarkup:
    price = subscription.price_for_owner(storage, owner_id)
    rows: list[list[InlineKeyboardButton]] = []
    if subscription.can_start_trial(storage, owner_id):
        trial_days = storage.get_global().trial_days
        rows.append(
            [InlineKeyboardButton(text=f"🎁 Пробный период ({trial_days} дн., бесплатно)", callback_data="sub:trial")]
        )
    rows.append([InlineKeyboardButton(text=f"⭐ Оплатить {price} звёзд / мес", callback_data="sub:pay")])
    rows.append([InlineKeyboardButton(text="🎟 Ввести промокод", callback_data="sub:promo")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="us:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "sub:menu")
async def sub_menu(call: CallbackQuery, storage: Storage) -> None:
    owner_id = call.from_user.id
    storage.db.ensure_owner(owner_id, is_admin=storage.is_admin(owner_id))
    await call.message.edit_text(_status_text(storage, owner_id), reply_markup=_status_keyboard(storage, owner_id))
    await call.answer()


@router.callback_query(F.data == "sub:trial")
async def sub_trial(call: CallbackQuery, storage: Storage) -> None:
    owner_id = call.from_user.id
    ok = subscription.start_trial(storage, owner_id)
    if not ok:
        await call.answer("Пробный период уже был использован.", show_alert=True)
        return
    await call.answer("🎁 Пробный период включён!")
    await call.message.edit_text(_status_text(storage, owner_id), reply_markup=_status_keyboard(storage, owner_id))


@router.callback_query(F.data == "sub:pay")
async def sub_pay(call: CallbackQuery, storage: Storage) -> None:
    owner_id = call.from_user.id
    price = subscription.price_for_owner(storage, owner_id)
    await call.answer()
    await call.bot.send_invoice(
        chat_id=call.message.chat.id,
        title="Полный доступ на 1 месяц",
        description="Снимает все ограничения бесплатного тарифа на 30 дней.",
        payload=f"sub_month:{owner_id}",
        currency=STARS_CURRENCY,
        prices=[LabeledPrice(label="Подписка на 1 месяц", amount=price)],
    )


@router.callback_query(F.data == "sub:promo")
async def sub_promo(call: CallbackQuery) -> None:
    _promo_pending.add(call.from_user.id)
    await call.answer()
    await call.message.answer("Введите промокод одним сообщением:")


async def handle_promo_input(message: Message, storage: Storage) -> bool:
    if message.from_user.id not in _promo_pending:
        return False
    _promo_pending.discard(message.from_user.id)
    code = (message.text or "").strip()
    if not code:
        await message.answer("❌ Промокод должен быть текстом.")
        return True
    _ok, text = subscription.redeem_promo(storage, message.from_user.id, code)
    await message.answer(text)
    return True


@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout: PreCheckoutQuery) -> None:
    await pre_checkout.answer(ok=True)


@router.message(F.successful_payment)
async def process_successful_payment(message: Message, storage: Storage) -> None:
    payment = message.successful_payment
    owner_id = message.from_user.id
    subscription.extend_paid(storage, owner_id, 30)
    storage.sub_set_discount(owner_id, None)  # скидка одноразовая, гасим после использования
    storage.payment_add(owner_id, payment.total_amount, payment.telegram_payment_charge_id)
    await message.answer(
        f"✅ Оплата на {payment.total_amount} ⭐ получена. Полный доступ продлён на 30 дней."
    )
