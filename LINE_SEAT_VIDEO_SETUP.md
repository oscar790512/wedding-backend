# LINE Seat Video Setup

This backend can reply to a LINE Official Account seat lookup message.

Flow:

1. Guest sends the lookup keyword to the LINE Official Account.
2. Guest sends their phone last five digits, or sends the keyword and digits in
   one message.
3. LINE sends a webhook to `POST /api/line/webhook`.
4. The backend verifies `x-line-signature`.
5. The backend looks up the attending guest by phone last five digits.
6. If the guest has an assigned table, LINE receives:
   - a text message with guest name, phone last five digits, table, and attendee count
   - the pre-rendered MP4 for that table
7. If the guest is unassigned, LINE receives the text message with `座位安排中，請洽現場工作人員`.

## Required Environment Variables

Set these on the backend deployment:

```env
PUBLIC_BASE_URL=https://your-backend.example.com
LINE_CHANNEL_ACCESS_TOKEN=your-line-channel-access-token
LINE_CHANNEL_SECRET=your-line-channel-secret
LINE_SEAT_LOOKUP_KEYWORD=我坐哪啊？
```

`PUBLIC_BASE_URL` must be the public HTTPS backend origin. LINE video messages use it to build URLs such as:

```text
https://your-backend.example.com/static/seat-videos/table-03.mp4
https://your-backend.example.com/static/seat-videos/table-03.png
```

## LINE Console Webhook URL

Use this webhook URL in the LINE Developers Console:

```text
https://your-backend.example.com/api/line/webhook
```

## Guest Message Examples

Two-step lookup:

```text
我坐哪啊？
```

The bot replies:

```text
請輸入電話後五碼，例如：45678
```

Then the guest sends:

```text
45678
```

One-message lookup:

```text
我坐哪啊？ 45678
```

If the guest sends only `45678` before entering lookup mode, the bot asks them
to send the keyword first.

## Regenerate Table Videos

From `wedding-backend`:

```sh
.venv/bin/python scripts/generate_seat_videos.py --source ../wedding_seats.jpg --output-dir app/static/seat-videos
```

The script outputs:

- `table-01.mp4` through `table-28.mp4`
- `table-01.png` through `table-28.png`
- `main-table.mp4`
- `main-table.png`
- `manifest.json`
- `source-floor-plan.jpg`

The videos use the uploaded real floor plan image as the background, then zoom to
the target table and flash the table ring five times.

The uploaded floor plan contains 27 regular tables plus `主桌`, for 28 total
tables. The API maps `第 3 桌` to `table-03.mp4`; `主桌` maps to
`main-table.mp4`. `table-28.mp4` is also generated at the main table position as
a compatibility fallback if table data contains `第 28 桌`.

To regenerate only one video while tuning a coordinate:

```sh
.venv/bin/python scripts/generate_seat_videos.py --source ../wedding_seats.jpg --output-dir app/static/seat-videos --only table-03
```
