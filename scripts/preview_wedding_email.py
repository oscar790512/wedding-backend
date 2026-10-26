"""Generate an offline preview using synthetic guest data. Never connects to SMTP/DB."""
import argparse
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.schemas.email import WeddingEmailContent
from app.services.wedding_email import qr_png, render_email

parser = argparse.ArgumentParser()
parser.add_argument('--output', default='previews/wedding-email.html')
parser.add_argument('--fixture-json')
args = parser.parse_args()
guest = {'id': '00000000-0000-4000-8000-000000000001', 'name': '王小明', 'email': 'guest@example.com', 'checkin_token': 'preview-only-not-a-real-checkin-token'}
content = WeddingEmailContent()
image = 'data:image/png;base64,' + base64.b64encode(qr_png(guest['checkin_token'])).decode()
html, text = render_email(guest, content, image)
output = Path(args.output)
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(html)
if args.fixture_json:
    fixture = {'content': content.model_dump(), 'html': html, 'text': text, 'subject': content.subject,
               'recipient': {'name': guest['name'], 'email': guest['email']}, 'total': 2,
               'batch_id': '10000000-0000-4000-8000-000000000001', 'preview_token': 'test-only'}
    Path(args.fixture_json).write_text(json.dumps(fixture, ensure_ascii=False, indent=2))
print(output.resolve())
