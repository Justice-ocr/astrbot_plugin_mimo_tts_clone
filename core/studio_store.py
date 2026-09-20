from __future__ import annotations

import json
import math
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class StudioStore:
    """Separate bounded generation history from recoverable delivery jobs."""

    PAGE_SIZE = 10

    def __init__(self, data_dir: str | Path):
        self.root = Path(data_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "studio.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS history (
                    id TEXT PRIMARY KEY, created REAL NOT NULL, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS history_order ON history(created DESC, id DESC);
                CREATE TABLE IF NOT EXISTS settings (
                    category TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(category, id)
                );
                CREATE TABLE IF NOT EXISTS lyrics (
                    id TEXT PRIMARY KEY, created REAL NOT NULL, payload TEXT NOT NULL
                );
            """)
            if not db.execute("SELECT 1 FROM settings WHERE category='defaults' AND id='style_presets_v1'").fetchone():
                for key, name, context in (
                    ("natural", "自然对话", "自然、清晰、亲切，语速适中，避免播音腔。"),
                    ("gentle", "温柔轻声", "温柔轻声，舒缓但清晰，保持自然呼吸和停顿。"),
                    ("narration", "故事旁白", "用有画面感的叙述语气，节奏有层次，句尾自然收束。"),
                    ("news", "清晰播报", "稳健准确地播报，吐字清楚，数字与专有名词适当放慢。"),
                    ("song_soft", "抒情歌曲", "抒情流行曲风，旋律舒展，咬字清晰，情感克制。"),
                ):
                    db.execute("INSERT OR IGNORE INTO settings VALUES ('styles', ?, ?)",
                               (key, json.dumps({"name": name, "context": context}, ensure_ascii=False)))
                db.execute("INSERT INTO settings VALUES ('defaults', 'style_presets_v1', '{}')")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def add_history(self, **payload) -> str:
        key = uuid.uuid4().hex
        created = time.time()
        payload.update(id=key, created_at=created)
        with self.connect() as db:
            db.execute("INSERT INTO history VALUES (?, ?, ?)",
                       (key, created, json.dumps(payload, ensure_ascii=False)))
            limit = self.history_policy()["max_records"]
            db.execute("""DELETE FROM history WHERE id IN (
                SELECT id FROM history ORDER BY created DESC, id DESC LIMIT -1 OFFSET ?
            )""", (limit,))
        return key

    def history_policy(self) -> dict:
        saved = self.settings("defaults").get("history", {})
        return {"max_records": int(saved.get("max_records", 1000)),
                "retention_days": int(saved.get("retention_days", 30))}

    def prune_history(self) -> None:
        policy = self.history_policy()
        with self.connect() as db:
            db.execute("DELETE FROM history WHERE created < ?",
                       (time.time() - policy["retention_days"] * 86400,))
            db.execute("""DELETE FROM history WHERE id IN (
                SELECT id FROM history ORDER BY created DESC, id DESC LIMIT -1 OFFSET ?
            )""", (policy["max_records"],))

    def history(self, page: int = 1) -> dict:
        self.prune_history()
        with self.connect() as db:
            total = db.execute("SELECT COUNT(*) FROM history").fetchone()[0]
            pages = max(1, math.ceil(total / self.PAGE_SIZE))
            page = max(1, min(int(page), pages))
            rows = db.execute(
                "SELECT payload FROM history ORDER BY created DESC, id DESC LIMIT ? OFFSET ?",
                (self.PAGE_SIZE, (page - 1) * self.PAGE_SIZE),
            ).fetchall()
        return {"items": [self.public_history(json.loads(row[0])) for row in rows],
                "page": page, "pages": pages, "total": total, "page_size": self.PAGE_SIZE}

    def history_item(self, key: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM history WHERE id = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def audio_path(self, item: dict) -> Path | None:
        path = (self.root / "outputs" / str(item.get("file") or "")).resolve()
        root = (self.root / "outputs").resolve()
        if path.parent != root or path.suffix != ".wav" or not path.is_file():
            return None
        return path

    def public_history(self, item: dict) -> dict:
        item = dict(item)
        item["available"] = self.audio_path(item) is not None
        item.pop("file", None)
        return item

    def delete_history(self, key: str | None = None) -> None:
        with self.connect() as db:
            if key is None:
                db.execute("DELETE FROM history")
            else:
                db.execute("DELETE FROM history WHERE id = ?", (key,))

    def settings(self, category: str) -> dict:
        with self.connect() as db:
            rows = db.execute("SELECT id, payload FROM settings WHERE category = ?",
                              (category,)).fetchall()
        return {key: json.loads(value) for key, value in rows}

    def save_lyrics(self, text: str, *, title: str = "", parent_id: str = "", prompt: str = "") -> dict:
        text = text.strip()
        if not text or len(text) > 20000:
            raise ValueError("歌词不能为空或超过 20000 字")
        item = {"id": uuid.uuid4().hex, "created_at": time.time(), "text": text,
                "title": title[:100] or text.splitlines()[0][:40],
                "parent_id": parent_id, "prompt": prompt[:4000]}
        with self.connect() as db:
            if parent_id and not db.execute("SELECT 1 FROM lyrics WHERE id = ?", (parent_id,)).fetchone():
                raise ValueError("原歌词版本不存在")
            db.execute("INSERT INTO lyrics VALUES (?, ?, ?)",
                       (item["id"], item["created_at"], json.dumps(item, ensure_ascii=False)))
            db.execute("""DELETE FROM lyrics WHERE id IN (
                SELECT id FROM lyrics ORDER BY created DESC LIMIT -1 OFFSET 200
            )""")
        return item

    def lyrics(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM lyrics ORDER BY created DESC LIMIT 200").fetchall()
        return [json.loads(row[0]) for row in rows]

    def delete_lyrics(self, key: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM lyrics WHERE id = ?", (key,))

    def save_setting(self, category: str, key: str, value: dict | None) -> None:
        if category not in {"styles", "sessions", "defaults"} or not key or len(key) > 256:
            raise ValueError("Invalid studio setting")
        with self.connect() as db:
            if value is None:
                db.execute("DELETE FROM settings WHERE category = ? AND id = ?", (category, key))
            else:
                db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?, ?)",
                           (category, key, json.dumps(value, ensure_ascii=False)))
