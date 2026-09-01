-- 报告单自定义打印版式（平台管理员为各医院配置）
-- config_json 结构（v1）：
--   header:  { layout, hospital_lines[], show_logo, title_mode, title_fixed }
--   patient: { fields[], columns }
--   table:   { columns, show_no, result_align, gap_px, min_rows }
--   footer:  { items[], note }
--   paper:   { size }
CREATE TABLE print_template (
  id            TEXT PRIMARY KEY,
  hospital_id   TEXT NOT NULL REFERENCES hospital(id),
  name          TEXT NOT NULL,
  config_json   TEXT NOT NULL,
  is_default    INTEGER NOT NULL DEFAULT 0,
  is_active     INTEGER NOT NULL DEFAULT 1,
  created_by    TEXT REFERENCES user_account(id),
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_print_template_hospital ON print_template(hospital_id, is_active);
