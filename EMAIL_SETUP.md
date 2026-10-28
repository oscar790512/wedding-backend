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
WEDDING_INVITATION_URL=https://wedding-frontend-ochre.vercel.app/invitation
```

`465` 使用 SSL；`587` 使用 STARTTLS，兩種均驗證 TLS 憑證。寄件地址固定使用 SMTP 登入帳號。後端主機需允許對外連線至指定 SMTP 連接埠。部署後端時安裝更新後的 `requirements.txt`，前端照原本流程 build/deploy。

`SMTP_DAILY_LIMIT` 是本系統滾動 24 小時的寄送**嘗試**上限，預設 450，並非 Gmail 剩餘額度。其他裝置／網頁寄信也會消耗 Google 帳號額度；超限或被拒絕時系統會暫停。全帳號批次共用資料庫額度保留與發送間隔。此額度限制和間隔不會保證 Gmail 接受所有信件。

## 模板與報到碼

暖色信件包含邀請文稿、2026/11/8 12:00 入場／12:30 午宴、老新台菜十全店 2 樓 A 廳與地址、停車建議／導航、電子喜帖、新人 LINE 連結與 ID、專屬報到 QR Code。預設資料沿用現有 RSVP／喜帖，內容可在寄送前編輯，每批存下確認時的模板。

停車文案只提供位置與提早抵達建議，不承諾車位、免費停車或折抵；以現場指引為準。停車場位置參考 [高雄市交通局登記資料](https://www.tbkc.gov.tw/FileOutput/Page/%E9%AB%98%E9%9B%84%E5%B8%82%E5%90%88%E6%A0%BC%E7%99%BB%E8%A8%98%E8%B7%AF%E5%A4%96%E6%B0%91%E7%87%9F%E5%81%9C%E8%BB%8A%E5%A0%B4%E4%B8%80%E8%A6%BD%E8%A1%A8%28%E6%9B%B4%E6%96%B0%E8%87%B3115%E5%B9%B44%E6%9C%88%E5%BA%95%29.pdf?id=a70f878a-6d93-4e0c-a4c7-f25336d4c7f6)。

每人一封，不公開其他收件人。報到圖片在後端產生，編碼為現有掃描器支援的 `wedding-checkin:<token>`，透過 CID 隨信內嵌 PNG，圖片名稱 `checkin-qr.png`，另有純文字版本。使用者應先存圖，現場由工作人員掃描，自行掃描不會自動報到。若一筆賓客登記含同行親友，此碼對應同一筆報到資料。

## 寄送紀錄與中斷

- 預覽簽章有效 30 分鐘，綁定收件名單、姓名、Email、報到碼、內容和寄件帳號。確認前資料變更需重新預覽。
- 以本次婚禮 campaign + guest ID 保證同一賓客不重複排入；重複點擊同一確認請求亦不重複建立紀錄。不同賓客若共用 Email，仍各收一封自己的報到碼。
- 領取任務使用固定 request ID；資料庫已保留任務但回覆遺失時，重試會取回同一筆，不多扣寄送次數，也不會再領下一位。寫回寄送結果只重試帶有同一次嘗試時間條件的資料庫更新，不重試 SMTP。
- 僅處理未刪除、確認出席、有 Email 與報到碼的賓客；實際發送前再次比對資料。
- `Gmail 已接受` 代表 SMTP 接受，並不保證進入收件匣；之後仍可能有退信。請留意寄件帳號信箱。
- SMTP 明確拒絕為「寄送失敗」，可按「只重試失敗信件」。若修正過賓客資料，請重新選取並預覽，建立新批次。
- 網路在提交時中斷或服務重啟，無法判定是否寄出的信件標為「待人工確認」，不自動重寄。請先至 Gmail 寄件備份核對；UI 不提供強制重寄此類信件。
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
