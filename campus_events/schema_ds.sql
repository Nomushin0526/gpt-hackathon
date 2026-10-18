-- 追加機能専用。既存のイベント・申込は変更しない。
-- 同じブラウザーで同じイベントを同日に再読込しても、閲覧は1件。
CREATE TABLE IF NOT EXISTS event_views (
    visitor_id TEXT NOT NULL,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    view_date TEXT NOT NULL,
    first_viewed_at TEXT NOT NULL,
    last_viewed_at TEXT NOT NULL,
    PRIMARY KEY (visitor_id, event_id, view_date)
);
CREATE INDEX IF NOT EXISTS idx_event_views_event_date ON event_views(event_id, view_date);
CREATE INDEX IF NOT EXISTS idx_event_views_date ON event_views(view_date);
CREATE INDEX IF NOT EXISTS idx_event_views_visitor_date ON event_views(visitor_id, view_date);

CREATE TABLE IF NOT EXISTS event_favorites (
    visitor_id TEXT NOT NULL,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (visitor_id, event_id)
);
CREATE INDEX IF NOT EXISTS idx_event_favorites_event ON event_favorites(event_id);
