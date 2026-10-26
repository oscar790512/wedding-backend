-- Apply once in the Supabase SQL editor before enabling SMTP delivery.
CREATE TABLE IF NOT EXISTS wedding_email_batches (
  id uuid PRIMARY KEY,
  campaign text NOT NULL,
  sender text NOT NULL,
  created_by text NOT NULL,
  content jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  last_error text
);
CREATE TABLE IF NOT EXISTS wedding_email_deliveries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  batch_id uuid NOT NULL REFERENCES wedding_email_batches(id),
  campaign text NOT NULL,
  guest_id uuid NOT NULL REFERENCES guests(id),
  name text NOT NULL,
  email text NOT NULL,
  checkin_token text NOT NULL,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sending','sent','failed','unknown')),
  error text,
  attempted_at timestamptz,
  sent_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (campaign, guest_id)
);
CREATE TABLE IF NOT EXISTS wedding_email_attempts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  delivery_id uuid NOT NULL REFERENCES wedding_email_deliveries(id),
  sender text NOT NULL,
  started_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS wedding_email_attempts_sender_time ON wedding_email_attempts(sender, started_at);
CREATE INDEX IF NOT EXISTS wedding_email_deliveries_batch ON wedding_email_deliveries(batch_id, status);
ALTER TABLE wedding_email_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE wedding_email_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE wedding_email_attempts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON wedding_email_batches, wedding_email_deliveries, wedding_email_attempts FROM anon, authenticated;
GRANT ALL ON wedding_email_batches, wedding_email_deliveries, wedding_email_attempts TO service_role;

CREATE OR REPLACE FUNCTION enqueue_wedding_email(p_id uuid, p_campaign text, p_sender text, p_actor text, p_content jsonb, p_guests jsonb)
RETURNS uuid LANGUAGE plpgsql SET search_path = public AS $$
DECLARE new_id uuid;
BEGIN
  INSERT INTO wedding_email_batches(id, campaign, sender, created_by, content)
    VALUES (p_id, p_campaign, p_sender, p_actor, p_content) ON CONFLICT (id) DO NOTHING RETURNING id INTO new_id;
  IF new_id IS NULL THEN RETURN p_id; END IF;
  -- Only the first confirmation creates deliveries. Replaying the request is safe.
  INSERT INTO wedding_email_deliveries(batch_id, campaign, guest_id, name, email, checkin_token)
    SELECT p_id, p_campaign, (g->>'id')::uuid, g->>'name', g->>'email', g->>'checkin_token'
    FROM jsonb_array_elements(p_guests) g ON CONFLICT (campaign, guest_id) DO UPDATE
      SET batch_id=EXCLUDED.batch_id, name=EXCLUDED.name, email=EXCLUDED.email,
          checkin_token=EXCLUDED.checkin_token, status='pending', error=NULL
      WHERE wedding_email_deliveries.status='failed';
  RETURN p_id;
END $$;

CREATE OR REPLACE FUNCTION claim_wedding_email(p_batch uuid, p_sender text, p_limit integer, p_interval double precision)
RETURNS jsonb LANGUAGE plpgsql SET search_path = public AS $$
DECLARE delivery wedding_email_deliveries; used integer; last_attempt timestamptz; wait_seconds double precision;
BEGIN
  -- Shared across all workers/batches sending from this account.
  PERFORM pg_advisory_xact_lock(hashtext('wedding_email:' || p_sender));
  UPDATE wedding_email_deliveries SET status='unknown', error='寄送中斷，請先至 Gmail 寄件備份確認，勿直接重寄。'
    WHERE status='sending' AND attempted_at < now() - interval '5 minutes';
  SELECT * INTO delivery FROM wedding_email_deliveries WHERE batch_id=p_batch AND status='pending'
    ORDER BY created_at, id FOR UPDATE SKIP LOCKED LIMIT 1;
  IF NOT FOUND THEN RETURN jsonb_build_object('state','empty'); END IF;
  SELECT count(*), max(started_at) INTO used, last_attempt FROM wedding_email_attempts
    WHERE sender=p_sender AND started_at > now()-interval '24 hours';
  IF used >= p_limit THEN RETURN jsonb_build_object('state','quota'); END IF;
  wait_seconds := p_interval - extract(epoch FROM now()-last_attempt);
  IF wait_seconds > 0 THEN RETURN jsonb_build_object('state','wait','seconds',wait_seconds); END IF;
  UPDATE wedding_email_deliveries SET status='sending', error=NULL, attempted_at=now() WHERE id=delivery.id RETURNING * INTO delivery;
  INSERT INTO wedding_email_attempts(delivery_id,sender) VALUES(delivery.id,p_sender);
  RETURN jsonb_build_object('state','claimed','delivery',to_jsonb(delivery));
END $$;
REVOKE ALL ON FUNCTION enqueue_wedding_email(uuid,text,text,text,jsonb,jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION claim_wedding_email(uuid,text,integer,double precision) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION enqueue_wedding_email(uuid,text,text,text,jsonb,jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION claim_wedding_email(uuid,text,integer,double precision) TO service_role;
NOTIFY pgrst, 'reload schema';
