"""Check signed relay access, receipt storage and SMTP login. Never sends an email."""
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import settings
from app.services.email_relay import relay_client_ready, signature

if not relay_client_ready() or not settings.email_relay_url.endswith('/api/internal/email-relay/send'):
    sys.exit('請設定 SMTP_USERNAME、完整 HTTPS EMAIL_RELAY_URL 與至少 32 字元的 EMAIL_RELAY_SECRET。')
body = b'{}'
timestamp = str(int(time.time()))
try:
    response = httpx.post(settings.email_relay_url.removesuffix('/send') + '/check', content=body,
                          headers={'Content-Type': 'application/json', 'X-Relay-Timestamp': timestamp,
                                   'X-Relay-Signature': signature(timestamp, body)},
                          timeout=120, follow_redirects=False)
    response.raise_for_status()
    result = response.json()
except (httpx.HTTPError, ValueError):
    sys.exit('無法取得代寄端診斷結果，請檢查 HTTPS URL、簽章金鑰與服務狀態。未寄出信件。')
if result.get('status') != 'ready' or result.get('sender') != settings.smtp_username.lower():
    reasons = {'storage': '代寄紀錄 migration 尚未完成或資料庫無法連線', 'setup': '代寄端 SMTP 設定未完成',
               'auth': '代寄端 Gmail 應用程式密碼驗證失敗', 'connection': '代寄端 SMTP 連線失敗'}
    sys.exit(reasons.get(result.get('reason'), '代寄端寄件帳號不一致或設定未完成') + '。未寄出信件。')
print('RELAY_READY：HTTPS 驗證、代寄紀錄與 Gmail SMTP 登入皆成功。未寄出信件。')
