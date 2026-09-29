DROP TABLE IF EXISTS events;

-- 公開イベントと学生からの応募を 1 テーブルで管理する。
-- status: pending(審査待ち) / approved(公開中) / rejected(差し戻し)
-- source: office(学生課が登録・紙掲示物の転載を含む) / student(学生・団体からの応募)
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    category TEXT NOT NULL,
    start_at TEXT NOT NULL,
    end_at TEXT,
    location TEXT NOT NULL,
    organizer_name TEXT NOT NULL,
    organizer_type TEXT NOT NULL,
    contact_email TEXT,
    capacity INTEGER,
    requires_registration INTEGER NOT NULL DEFAULT 0,
    registration_info TEXT,
    note_to_office TEXT,
    poster_filename TEXT,
    source TEXT NOT NULL DEFAULT 'student',
    status TEXT NOT NULL DEFAULT 'pending',
    review_comment TEXT,
    receipt_code TEXT UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_events_status_start ON events (status, start_at);
