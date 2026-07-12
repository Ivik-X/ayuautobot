from __future__ import annotations

import html
import time

_MEDIA_LABELS = {
    "photo": "📷 Фото",
    "video": "🎬 Видео",
    "voice": "🎤 Голосовое",
    "video_note": "⭕ Кружок",
    "audio": "🎵 Аудио",
    "document": "📎 Файл",
    "sticker": "🙂 Стикер",
    "animation": "GIF",
}


def _fmt_iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


def build_export_json(chat_title: str, chat_id: int, rows: list) -> dict:
    """Собирает JSON в структуре, близкой к официальному экспорту Telegram Desktop
    (result.json: name/type/id/messages[]). Строится только из того, что бот
    сам успел закэшировать/сохранить, пока был подключён — это не полная
    ретроактивная история чата (Bot API её просто не даёт), а журнал с
    момента, когда бот начал слушать этот чат.
    """
    messages = []
    for row in rows:
        entry: dict = {
            "id": row["message_id"],
            "type": "message",
            "date": _fmt_iso(row["cached_at"]),
            "date_unixtime": str(int(row["cached_at"])),
            "from": row["from_user_name"] or "",
            "from_id": f"user{row['from_user_id']}" if row["from_user_id"] else "",
            "text": row["content"] or "",
        }
        if row["edited_at"]:
            entry["edited"] = _fmt_iso(row["edited_at"])
        if row["deleted_at"]:
            entry["deleted"] = _fmt_iso(row["deleted_at"])
        if row["media_kind"]:
            entry["media_type"] = row["media_kind"]
            if row["media_path"]:
                entry["file"] = row["media_path"]
        messages.append(entry)

    return {"name": chat_title, "type": "personal_chat", "id": chat_id, "messages": messages}


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
body {{ background:#0e1621; color:#e2e6ea; font-family: -apple-system, Roboto, Arial, sans-serif; margin:0; padding:20px; }}
.chat-title {{ text-align:center; color:#8b98a5; margin-bottom:20px; font-size:14px; }}
.msg {{ max-width:60%; margin:6px 0; padding:8px 12px; border-radius:14px; line-height:1.4; font-size:14px; word-wrap:break-word; white-space:pre-wrap; }}
.msg.in {{ background:#182533; margin-right:auto; border-bottom-left-radius:4px; }}
.msg.out {{ background:#2b5278; margin-left:auto; border-bottom-right-radius:4px; }}
.meta {{ font-size:11px; color:#8b98a5; margin-top:4px; text-align:right; }}
.sender {{ font-size:12px; color:#6ab3f3; margin-bottom:2px; font-weight:600; }}
.deleted {{ opacity:0.55; font-style:italic; }}
.media-tag {{ display:inline-block; background:#0e1621; border-radius:6px; padding:2px 8px; font-size:12px; margin-bottom:4px; }}
</style>
</head>
<body>
<div class="chat-title">💬 {title} — экспорт переписки (только то, что сохранил бот)</div>
{body}
</body>
</html>"""


def build_export_html(chat_title: str, owner_user_id: int | None, rows: list) -> str:
    parts: list[str] = []
    for row in rows:
        is_out = owner_user_id is not None and row["from_user_id"] == owner_user_id
        css = "out" if is_out else "in"
        deleted_cls = " deleted" if row["deleted_at"] else ""
        media_tag = ""
        if row["media_kind"]:
            label = _MEDIA_LABELS.get(row["media_kind"], "📦 Медиа")
            media_tag = f'<div class="media-tag">{label}</div>'
        text = html.escape(row["content"] or "")
        sender = html.escape(row["from_user_name"] or "?")
        ts = time.strftime("%d.%m.%Y %H:%M", time.localtime(row["cached_at"]))
        suffix = " (удалено)" if row["deleted_at"] else (" (изменено)" if row["edited_at"] else "")
        parts.append(
            f'<div class="msg {css}{deleted_cls}"><div class="sender">{sender}</div>'
            f'{media_tag}{text}<div class="meta">{ts}{suffix}</div></div>'
        )
    return _HTML_TEMPLATE.format(title=html.escape(chat_title), body="\n".join(parts))
