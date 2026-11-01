# Gmail SMTP 婚禮通知

管理員登入後，側欄新增「Email 通知」（`/admin/email`）。先選擇確認出席的賓客、調整模板，按「一鍵寄送 Email」預覽，再按「確認寄送」建立背景批次。預覽不寫入資料庫，也不寄信；未設定 SMTP 時仍可預覽。

## 首次啟用

1. 先部署 **wedding-database repo** 的版本化 migration。此版 backend 依賴 `supabase/migrations/20261028000000_retryable_email_claim.sql`（任務領取 v2），部署順序為 DB → Backend → Frontend；詳細流程見 [DB repo 說明](https://github.com/oscar790512/wedding-database/blob/main/README.md)。新 migration 僅在 DB repo 維護，backend 原有 `20261026_wedding_email.sql` 是舊版相容副本，不作為部署來源。
2. Google 帳號啟用兩步驟驗證，建立 [應用程式密碼](https://myaccount.google.com/apppasswords)。若帳號無此選項，先確認組織政策／帳號設定是否允許。
3. 在後端部署平台的環境變數設定下列項目；本機開發則填在被 Git 忽略的 `.env`。勿把密碼放進前端或提交至 Git。

```dotenv
SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_USERNAME=your-account@gmail.com
SMTP_PASSWORD=your-google-app-password
SMTP_SENDER_NAME=祺元 & 姵妤
SMTP_DAILY_LIMIT=450
SMTP_INTERVAL_SECONDS=2
WEDDING_INVITATION_URL=https://oscar-peiyu-wedding.vercel.app/invitation
```

`465` 使用 SSL；`587` 使用 STARTTLS，兩種均驗證 TLS 憑證。寄件地址固定使用 SMTP 登入帳號。後端主機需允許對外連線至指定 SMTP 連接埠。部署後端時安裝更新後的 `requirements.txt`，前端照原本流程 build/deploy。

`SMTP_DAILY_LIMIT` 是本系統滾動 24 小時的寄送**嘗試**上限，預設 450，並非 Gmail 剩餘額度。其他裝置／網頁寄信也會消耗 Google 帳號額度；超限或被拒絕時系統會暫停。全帳號批次共用資料庫額度保留與發送間隔。此額度限制和間隔不會保證 Gmail 接受所有信件。

## 正式環境透過 Staging HTTPS 代寄

正式環境保留完整名單、預覽簽章、批次與寄送結果，僅把當封已產生的 MIME 信件（含個人桌次、QR 圖）經 TLS 傳給 staging 的寄信 API。Staging 使用自己的 SMTP 帳號與自己的 Supabase；不需要正式資料庫金鑰，也不查詢 staging 賓客名單。收件人收到的 HTML、純文字與 QR 圖都沿用正式環境確認的內容。

啟用順序：

1. 先部署 **wedding-database** 的 `20261101000000_wedding_email_relay.sql`，依既有流程部署 staging / production。新表只保留請求 UUID、寄件帳號、內容 SHA-256、狀態與時間，不保存 MIME、收件地址或 QR token。表開啟 RLS，僅 service role 可操作。
2. 部署新版 backend 至兩個環境。未設定代寄參數時，既有直接 SMTP 流程不變；新端點預設停用。
3. 產生專用隨機共用密鑰（例如 `openssl rand -hex 32`），只放進兩個服務的私密環境變數。不要沿用 JWT、Supabase 或 Gmail 密碼，也不要放到前端／Git。
4. 在 **Northflank staging** 設定：

   ```dotenv
   EMAIL_RELAY_ENABLED=true
   EMAIL_RELAY_SECRET=<專用共用密鑰，至少32字元>
   EMAIL_RELAY_URL=
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=465
   SMTP_USERNAME=<Gmail寄件帳號>
   SMTP_PASSWORD=<Gmail應用程式密碼>
   ```

5. 在 **Render production** 設定：

   ```dotenv
   EMAIL_RELAY_ENABLED=false
   EMAIL_RELAY_URL=https://<Northflank staging 網域>/api/internal/email-relay/send
   EMAIL_RELAY_SECRET=<與staging相同的專用共用密鑰>
   SMTP_USERNAME=<與staging相同的Gmail寄件帳號>
   ```

   Prod 不再需要使用 SMTP_PASSWORD，所有寄送走 HTTPS；不會在失敗時改走直接 SMTP。Staging 的 EMAIL_RELAY_URL 必須維持空白，自己原有的寄信流程仍走直接 SMTP。環境變數更新後需重新部署／重啟生效。

6. 在載入 **production 代寄設定** 的後端環境執行 `.venv/bin/python scripts/check_email_relay.py`。此命令呼叫有簽章的 `/api/internal/email-relay/check`，檢查 staging 代寄紀錄表、SMTP 連線及登入、寄件帳號一致性；不傳賓客內容、不建立寄送紀錄，也不寄信。成功會顯示 `RELAY_READY`。本機若載入 staging 的 `.env`，需以私密環境設定覆蓋上述 production 代寄參數後再執行，勿改動 Supabase 指向正式資料庫。
7. 確認診斷成功後，在正式頁面重新預覽，再由管理員確認寄送或重試明確失敗的信件。部署與診斷不會自動恢復批次。

請求使用 HMAC-SHA256，綁定原始 body 與時間戳（有效期 5 分鐘）；只接受設定的寄件帳號、單一收件人、無 Cc/Bcc、上限 1 MB。簽章失敗或端點未啟用時不會接觸資料庫／SMTP。請保持主機時間同步，不要把共用密鑰寫入 URL。

每次寄送嘗試的 Message-ID 對應固定請求 UUID。Staging 在 SMTP 前透過資料庫交易取得一次性的寄送權，重複／並行請求、程序重啟、回覆遺失都不會再取得寄送權。只有確定 Gmail 已接受且結果成功保存時，才回傳 `sent`；逾時、結果保存失敗或接手未完成的請求一律回傳 `unknown`。Prod 保留既有人工查證流程，不自動重寄。不應刪除或重設代寄紀錄來嘗試修復卡住的信件。

Staging 直接寄送與代寄請求共用該機器同一 Gmail 帳號的滾動額度與寄送間隔；正式環境也保留自己的額度上限。其他環境／Gmail 網頁自行寄出的信仍不在此共用計數中。代寄端達額度或忙碌時，本次尚未送出的信會標為失敗並暫停，需稍後手動重試。

Staging 停機、重新部署或資料庫不可用時，正式寄信也會暫停。保持 endpoint、共用密鑰、紀錄表穩定；回復直接 SMTP 可清空 production 的 EMAIL_RELAY_URL，但 Render Free 的 SMTP 限制仍存在。

## 模板、座位與報到碼

暖色信件包含邀請文稿、2026/11/8 12:00 入場／12:30 午宴、老新台菜十全店 2 樓 A 廳與地址、停車建議／導航、個人桌次、電子喜帖、新人 LINE 連結與 ID、專屬報到 QR Code。預設資料沿用現有 RSVP／喜帖，內容可在寄送前編輯，每批存下確認時的模板。

「為你留好的座位」自動帶入賓客的 `allocated_table` 及 `table_settings.table_number`，例如「第 8 桌｜幸福滿桌」；未設定桌號時顯示桌名，未安排桌次時提示洽現場報到人員。預覽簽章綁定桌名與桌號，確認後的座位快照保存在既有批次 JSON，不需新增 migration。寄送前若座位已變更，此封不會送出，需重新預覽。舊批次未保存座位快照，會顯示洽現場報到人員的提示；需要通知桌次時，應建立新預覽。

停車文案只提供位置與提早抵達建議，不承諾車位、免費停車或折抵；以現場指引為準。停車場位置參考 [高雄市交通局登記資料](https://www.tbkc.gov.tw/FileOutput/Page/%E9%AB%98%E9%9B%84%E5%B8%82%E5%90%88%E6%A0%BC%E7%99%BB%E8%A8%98%E8%B7%AF%E5%A4%96%E6%B0%91%E7%87%9F%E5%81%9C%E8%BB%8A%E5%A0%B4%E4%B8%80%E8%A6%BD%E8%A1%A8%28%E6%9B%B4%E6%96%B0%E8%87%B3115%E5%B9%B44%E6%9C%88%E5%BA%95%29.pdf?id=a70f878a-6d93-4e0c-a4c7-f25336d4c7f6)。

每人一封，不公開其他收件人。報到圖片在後端產生，編碼為現有掃描器支援的 `wedding-checkin:<token>`，透過 CID 隨信內嵌 PNG，圖片名稱 `checkin-qr.png`，另有純文字版本。使用者應先存圖，現場由工作人員掃描，自行掃描不會自動報到。若一筆賓客登記含同行親友，此碼對應同一筆報到資料。

## 寄送紀錄與中斷

- 預覽簽章有效 30 分鐘，綁定收件名單、姓名、Email、報到碼、桌名、桌號、內容和寄件帳號。確認前資料變更需重新預覽。
- 以本次婚禮 campaign + guest ID 保證同一賓客不重複排入；重複點擊同一確認請求亦不重複建立紀錄。不同賓客若共用 Email，仍各收一封自己的報到碼。
- 領取任務使用固定 request ID；資料庫已保留任務但回覆遺失時，重試會取回同一筆，不多扣寄送次數，也不會再領下一位。寫回寄送結果只重試帶有同一次嘗試時間條件的資料庫更新，不重試 SMTP。
- 僅處理未刪除、確認出席、有 Email 與報到碼的賓客；實際發送前再次比對資料。
- `Gmail 已接受` 代表 SMTP 接受，並不保證進入收件匣；之後仍可能有退信。請留意寄件帳號信箱。
- SMTP 明確拒絕為「寄送失敗」，可按「只重試失敗信件」。若修正過賓客資料，請重新選取並預覽，建立新批次。
- 網路在提交時中斷或服務重啟，無法判定是否寄出的信件標為「待人工確認」，不自動重寄。管理員先至 Gmail 寄件備份核對，再逐封按「確認未寄出」、勾選確認並標記，該筆才會轉為可重試。標記本身不啟動 SMTP，須另按「只重試失敗信件」或重新選取賓客預覽後寄送。
- 人工標記綁定該筆紀錄的寄送嘗試時間，只允許 `unknown` 轉為 `failed`；如果已開始另一輪嘗試、寄送成功或狀態不同，會拒絕舊操作。相同確認請求可安全重試，並在後端日誌記錄操作管理員、紀錄 ID 與嘗試時間，不記錄 Email 或報到碼。
- 服務重啟後，未發送項目保留在資料庫；開啟頁面可恢復查看紀錄，按「繼續寄送」恢復未發送項目。超過 5 分鐘仍在寄送中的紀錄會轉成待確認。
- 背景程序暫停時，前端仍持續更新尚在寄送中的項目，直到有明確結果；狀態查詢暫時失敗會稍後重試。一般進度查詢只讀資料，只有偵測到逾時項目時才更新其狀態。
- 排程由 FastAPI background task 執行，需使用可持續運作的後端服務。這個版本沒有外部排程器，不會在額度恢復／服務重啟後自行喚醒。

## 本機預覽與驗證

```sh
.venv/bin/python scripts/preview_wedding_email.py
.venv/bin/python -m unittest discover -s tests
```

[`previews/wedding-email.html`](previews/wedding-email.html) 及預覽 PNG 只含虛構賓客／測試報到碼，不可用於真實報到。可用 `--fixture-json ../wedding-frontend/e2e/fixtures/wedding-email.json` 重新產生前端測試資料。

SQL 回歸測試位於 **wedding-database repo**，使用獨立暫存安裝，不連正式資料庫。在該 repo 執行：

```sh
npm install --prefix /tmp/wedding-email-sql-validation @electric-sql/pglite
PGLITE_MODULE=file:///tmp/wedding-email-sql-validation/node_modules/@electric-sql/pglite/dist/index.js node --test tests/*.test.mjs
```

前端驗證：`npm test`、`npm run build`、`npm run test:e2e -- e2e/email.spec.js`。瀏覽器測試攔截全部寄送請求，檢查完整名單、預覽／確認、缺少 SMTP 時禁止寄送、重試行為、PNG QR 解碼與手機排版。
