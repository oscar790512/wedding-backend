import hashlib
import hmac
import io
import json
import smtplib
import ssl
import time
from contextlib import suppress
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from html import escape
from urllib.parse import quote

import qrcode

from app.config import settings

CAMPAIGN = "wedding-2026-11-08"
TEMPLATE_VERSION = 1


class SmtpSetupError(Exception):
    """Connection/setup failed before the message could be submitted."""


def qr_png(token: str) -> bytes:
    image = qrcode.make(f"wedding-checkin:{token}", box_size=8, border=4)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def render_email(guest: dict, content, image_src="cid:checkin-qr") -> tuple[str, str]:
    data = content.model_dump()
    e = {key: escape(value) for key, value in data.items()}
    name = escape(guest["name"])
    paragraphs = "".join(f'<p style="margin:0 0 16px">{escape(p)}</p>' for p in data["message"].splitlines() if p.strip())
    parking = e["parking"].replace("\n", "<br>")
    maps = "https://www.google.com/maps/search/?api=1&query=" + quote(data["address"])
    parking_maps = "https://www.google.com/maps/search/?api=1&query=" + quote("老新台菜十全店 停車場")
    button = 'display:inline-block;padding:12px 20px;border-radius:6px;background:#934e44;color:#fff;text-decoration:none;font-weight:bold;margin:6px 8px 6px 0'
    html = f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e['subject']}</title></head>
<body style="margin:0;background:#f7f0ea;color:#43352f;font-family:Arial,'Microsoft JhengHei',sans-serif;line-height:1.85">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="600" cellspacing="0" cellpadding="0" style="width:100%;max-width:600px;background:#fffdf9;border:1px solid #e6d6c8;border-radius:12px">
<tr><td style="padding:36px 26px;text-align:center;background:#934e44;color:#fff7ec"><p style="font-size:11px;letter-spacing:4px;margin:0">WE ARE GETTING MARRIED</p><h1 style="font-family:serif;font-size:32px;margin:12px 0">祺元 &amp; 姵妤</h1><p style="margin:0">2026.11.08 · 好久不見，婚禮見</p></td></tr>
<tr><td style="padding:30px 26px"><p style="font-size:19px"><strong>親愛的 {name}，</strong></p>{paragraphs}
<h2 style="font-size:20px;color:#934e44;border-bottom:1px solid #e6d6c8;padding-bottom:8px">把這天留給幸福</h2>
<p><strong>婚禮時間</strong><br>{e['wedding_time']}</p><p><strong>婚宴地點</strong><br>{e['venue']}<br>{e['address']}</p>
<a href="{escape(maps, quote=True)}" style="{button}">開啟婚宴導航</a>
<h2 style="font-size:20px;color:#934e44">開車赴約的小提醒</h2><p>{parking}</p><a href="{escape(parking_maps, quote=True)}" style="color:#934e44">查看附近停車場 →</a>
<h2 style="font-size:20px;color:#934e44">你的專屬報到 QR Code</h2>
<p>請提前將下方 QR Code 截圖或儲存到手機相簿。抵達現場後，向報到人員出示，由工作人員掃描即可協助完成報到，讓我們更快和你見面！</p>
<table role="presentation" width="100%" style="border:1px dashed #d6bcab;background:#fff"><tr><td align="center" style="padding:20px"><strong>{name} 的報到碼</strong><br><img src="{escape(image_src, quote=True)}" width="232" height="232" alt="{name} 的專屬報到 QR Code" style="display:block;max-width:100%;height:auto;margin:12px auto"><span style="font-size:13px">建議提前存圖 · 此碼供你與同行親友報到使用</span></td></tr></table>
<p style="font-size:13px;color:#7b6b64">若圖片未顯示，請查看信件附帶的 checkin-qr.png。這是你的專屬報到資訊，請妥善保存。手機自行掃碼不會完成報到。</p>
<h2 style="font-size:20px;color:#934e44">赴約前，先看看我們的喜帖</h2><a href="{e['invitation_url']}" style="{button}">開啟電子喜帖</a><p style="font-size:13px;word-break:break-all">{e['invitation_url']}</p>
<h2 style="font-size:20px;color:#934e44">和新人保持聯繫</h2><p>加入我們的 LINE 官方帳號，接收婚禮提醒與座位資訊；有任何問題，也歡迎在這裡和我們說一聲。</p><p><strong>LINE ID：{e['line_id']}</strong></p><a href="{e['line_url']}" style="{button};background:#33775b">加入新人 LINE 官方帳號</a><p style="font-size:13px;word-break:break-all">{e['line_url']}</p>
<p style="margin-top:32px">謝謝你，把這一天留給我們。<br>期待見到你！<br><strong>祺元 &amp; 姵妤 敬邀</strong></p></td></tr></table>
</td></tr></table></body></html>'''
    text = f"親愛的 {guest['name']}，\n\n{data['message']}\n\n婚禮時間：{data['wedding_time']}\n地點：{data['venue']}\n地址：{data['address']}\n導航：{maps}\n\n停車建議：\n{data['parking']}\n附近停車場：{parking_maps}\n\n請提前儲存隨信的 checkin-qr.png，抵達時出示給工作人員掃描報到。手機自行掃碼不會完成報到。\n\n電子喜帖：{data['invitation_url']}\nLINE 官方連結：{data['line_url']}\nLINE ID：{data['line_id']}\n\n期待見到你！\n祺元 & 姵妤 敬邀"
    return html, text


def preview_signature(batch_id, expires, guests, content):
    snapshot = {"batch_id": str(batch_id), "expires": expires, "guests": guests, "content": content.model_dump(), "version": TEMPLATE_VERSION, "sender": settings.smtp_username}
    value = json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()
    return f"{expires}." + hmac.new(settings.jwt_secret.encode(), value, hashlib.sha256).hexdigest()


def verify_preview(token, batch_id, guests, content):
    try:
        expires = int(token.split(".")[0])
        return expires >= int(time.time()) and hmac.compare_digest(token, preview_signature(batch_id, expires, guests, content))
    except (ValueError, TypeError):
        return False


def smtp_ready():
    return bool(settings.smtp_username and settings.smtp_password and settings.smtp_port in (465, 587))


def build_message(guest, content, batch_id):
    html, text = render_email(guest, content)
    message = EmailMessage()
    message["Subject"] = content.subject
    message["From"] = formataddr((settings.smtp_sender_name, settings.smtp_username))
    message["To"] = guest["email"]
    message["Message-ID"] = make_msgid(idstring=f"{batch_id}.{guest['id']}")
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    message.get_payload()[1].add_related(qr_png(guest["checkin_token"]), maintype="image", subtype="png", cid="<checkin-qr>", filename="checkin-qr.png", disposition="inline")
    return message


def send_message(message):
    context = ssl.create_default_context()
    client = None
    try:
        if settings.smtp_port == 465:
            client = smtplib.SMTP_SSL(settings.smtp_host, 465, timeout=30, context=context)
        else:
            client = smtplib.SMTP(settings.smtp_host, 587, timeout=30)
        if settings.smtp_port == 587:
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        client.login(settings.smtp_username, settings.smtp_password.replace(" ", ""))
    except Exception as exc:
        if client:
            with suppress(OSError):
                client.close()
        if isinstance(exc, smtplib.SMTPAuthenticationError):
            raise
        raise SmtpSetupError from exc
    try:
        client.send_message(message, from_addr=settings.smtp_username, to_addrs=[str(message["To"])])
    finally:
        # Closing the socket cannot turn an accepted message into a failed send.
        with suppress(OSError):
            client.close()
