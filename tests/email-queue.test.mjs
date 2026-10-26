// Optional SQL integration check; see EMAIL_SETUP.md for an isolated PGlite install.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
const { PGlite } = await import(process.env.PGLITE_MODULE || '@electric-sql/pglite')

const guest = (n, extra = {}) => ({ id: `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`, name: `Guest ${n}`, email: `guest${n}@example.com`, checkin_token: `token-${n}`, ...extra })
const batch = n => `10000000-0000-4000-8000-${String(n).padStart(12, '0')}`

test('durable wedding email queue', async t => {
  const db = await PGlite.create()
  try {
    await db.exec('CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role; CREATE TABLE guests(id uuid PRIMARY KEY);')
    for (let n = 1; n <= 4; n++) await db.query('INSERT INTO guests VALUES($1)', [guest(n).id])
    const migration = readFileSync(new URL('../migrations/20261026_wedding_email.sql', import.meta.url), 'utf8')
    await db.exec(migration)
    await db.exec(migration)
    const enqueue = (n, guests) => db.query('SELECT enqueue_wedding_email($1,$2,$3,$4,$5,$6)', [batch(n), 'test', 'sender@gmail.com', 'admin', JSON.stringify({ subject: 'test' }), JSON.stringify(guests)])
    const claim = async (n, limit = 450, interval = 0) => (await db.query('SELECT claim_wedding_email($1,$2,$3,$4) AS result', [batch(n), 'sender@gmail.com', limit, interval])).rows[0].result

    await t.test('confirmation replay and overlapping selections do not duplicate deliveries', async () => {
      await enqueue(1, [guest(1), guest(2), guest(3)])
      await enqueue(1, [guest(1), guest(2), guest(3)])
      await enqueue(2, [guest(1), guest(2)])
      assert.equal((await db.query('SELECT count(*)::int AS n FROM wedding_email_deliveries')).rows[0].n, 3)
      assert.equal((await claim(2)).state, 'empty')
    })
    let first
    await t.test('claims persist before SMTP and cannot claim the same delivery again', async () => {
      first = await claim(1)
      const second = await claim(1)
      assert.equal(first.state, 'claimed')
      assert.equal(second.state, 'claimed')
      assert.notEqual(first.delivery.id, second.delivery.id)
      assert.equal((await db.query('SELECT count(*)::int AS n FROM wedding_email_attempts')).rows[0].n, 2)
    })
    await t.test('daily cap includes reserved attempts and pauses before another SMTP send', async () => {
      assert.equal((await claim(1, 2)).state, 'quota')
      assert.equal((await db.query("SELECT count(*)::int AS n FROM wedding_email_deliveries WHERE status='pending'")).rows[0].n, 1)
    })
    await t.test('throttling is shared across batches', async () => {
      const result = await claim(1, 450, 100)
      assert.equal(result.state, 'wait')
      assert.ok(result.seconds > 0)
    })
    await t.test('only definitively failed deliveries can move to a newly reviewed batch', async () => {
      await db.query("UPDATE wedding_email_deliveries SET status='failed' WHERE id=$1", [first.delivery.id])
      const changedGuest = guest(Number(first.delivery.name.split(' ')[1]), { email: 'corrected@example.com' })
      await enqueue(3, [changedGuest, guest(4)])
      const result = await claim(3)
      assert.equal(result.state, 'claimed')
      const rows = (await db.query('SELECT * FROM wedding_email_deliveries WHERE guest_id=$1', [changedGuest.id])).rows
      assert.equal(rows[0].email, 'corrected@example.com')
      assert.equal(rows[0].batch_id, batch(3))
      assert.equal((await db.query('SELECT count(*)::int AS n FROM wedding_email_deliveries')).rows[0].n, 4)
    })
    await t.test('stale sending records become unknown and are excluded from re-enqueue', async () => {
      await db.exec("UPDATE wedding_email_deliveries SET attempted_at=now()-interval '10 minutes' WHERE status='sending'")
      await claim(1)
      const unknown = (await db.query("SELECT guest_id FROM wedding_email_deliveries WHERE status='unknown'")).rows
      assert.ok(unknown.length >= 1)
      await enqueue(4, unknown.map(row => ({ ...guest(1), id: row.guest_id })))
      assert.equal((await claim(4)).state, 'empty')
    })
    await t.test('public browser roles cannot read private QR tokens or call queue functions', async () => {
      const result = (await db.query("SELECT has_table_privilege('anon','wedding_email_deliveries','SELECT') AS anon_read, has_function_privilege('authenticated','claim_wedding_email(uuid,text,integer,double precision)','EXECUTE') AS user_claim, has_function_privilege('service_role','claim_wedding_email(uuid,text,integer,double precision)','EXECUTE') AS server_claim")).rows[0]
      assert.equal(result.anon_read, false)
      assert.equal(result.user_claim, false)
      assert.equal(result.server_claim, true)
    })
  } finally { await db.close() }
})
