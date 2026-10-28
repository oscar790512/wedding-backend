from uuid import UUID
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class WeddingEmailContent(BaseModel):
    subject: str = Field(default="祺元 & 姵妤的婚禮｜11/8 我們婚禮見！", min_length=1, max_length=180)
    message: str = Field(default="謝謝你願意在這個特別的日子，來和我們一起分享幸福！\n從相識、相伴，到決定牽手走進人生的下一站，一路上有你們的陪伴與祝福，是我們最珍惜的事。\n我們已經為你留好位置、準備好美食，期待當天和你聊聊天、拍拍照，一起留下溫暖又開心的回憶。\n以下整理了婚宴與報到資訊，出發前再看一眼，就能安心赴約。11 月 8 日，我們婚禮見！", min_length=1, max_length=6000)
    wedding_time: str = Field(default="2026 年 11 月 8 日（星期日）｜12:00 入場・12:30 午宴開始", min_length=1, max_length=300)
    venue: str = Field(default="老新台菜 十全店｜2 樓 A 廳", min_length=1, max_length=300)
    address: str = Field(default="高雄市三民區十全三路 265 號", min_length=1, max_length=300)
    parking: str = Field(default="開車的朋友可先前往老新台菜停車場（德裕街與十全三路口附近），依現場人員指引停放。\n建議預留 20–30 分鐘找車位與步行；若停車場已滿，可改找周邊合法收費停車場或路邊停車格。費用與優惠請依現場公告或向餐廳確認。\n也歡迎共乘或搭乘計程車，輕鬆抵達；當天若有飲酒，請勿開車。", min_length=1, max_length=2500)
    invitation_url: str = Field(default="https://wedding-frontend-ochre.vercel.app/invitation", max_length=1000)
    line_url: str = Field(default="https://lin.ee/PvW0Voh", max_length=1000)
    line_id: str = Field(default="@287rlhkg", min_length=1, max_length=100)

    @field_validator("subject", "wedding_time", "venue", "address", "line_id")
    @classmethod
    def single_line(cls, value):
        if "\r" in value or "\n" in value or not value.strip():
            raise ValueError("請填寫單行文字")
        return value.strip()

    @field_validator("invitation_url", "line_url")
    @classmethod
    def https_url(cls, value):
        from urllib.parse import urlsplit
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or any(c.isspace() for c in value):
            raise ValueError("請填寫有效的 HTTPS 網址")
        return value


class EmailPreviewRequest(BaseModel):
    guest_ids: list[UUID] = Field(min_length=1, max_length=2000)
    content: WeddingEmailContent
    preview_guest_id: UUID


class EmailSendRequest(EmailPreviewRequest):
    preview_token: str = Field(max_length=200)
    batch_id: UUID


class EmailResumeRequest(BaseModel):
    retry_failed: bool = False


class EmailManualResolution(BaseModel):
    confirmed_not_sent: bool = Field(strict=True)
    attempted_at: datetime

    @field_validator("confirmed_not_sent")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("請先確認這次信件未寄出")
        return value

    @field_validator("attempted_at")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError("請使用含時區的寄送嘗試時間")
        return value
