CREATE TABLE rejections (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  row_id TEXT,
  table_name TEXT,
  outcome TEXT NOT NULL,
  reason TEXT,
  raw_record JSONB,
  synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
