"""Persistencia SQLite: buffer MQTT, telemetria, eventos e energia diaria."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class Storage:
    def __init__(self, path: Path, settings: dict[str, Any]):
        self.path = path
        self.settings = settings
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        with self._lock, self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.execute("PRAGMA busy_timeout=30000")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS mqtt_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    qos INTEGER NOT NULL CHECK(qos IN (0,1)),
                    retain INTEGER NOT NULL CHECK(retain IN (0,1)),
                    created_at TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_mqtt_queue_ready ON mqtt_queue(next_attempt, id);
                CREATE TABLE IF NOT EXISTS telemetry_latest (
                    device_id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    quality INTEGER NOT NULL,
                    sampled_at TEXT NOT NULL,
                    updated_monotonic REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    source TEXT NOT NULL,
                    code TEXT NOT NULL,
                    message TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_events_created ON events(id DESC);
                CREATE TABLE IF NOT EXISTS daily_energy (
                    device_id TEXT NOT NULL,
                    field_id TEXT NOT NULL,
                    local_date TEXT NOT NULL,
                    baseline REAL NOT NULL,
                    last_total REAL NOT NULL,
                    daily_value REAL NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(device_id, field_id)
                );
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            self._connection.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version','1')")
            self._compact_retained_locked()

    def _compact_retained_locked(self) -> int:
        # Num topico retido o broker so guarda a ultima mensagem. Replay de
        # historico retido e inutil, e a rajada (2 mil heartbeats seguidos em
        # Betania) fazia a AWS IoT parar de devolver PUBACK.
        cursor = self._connection.execute(
            "DELETE FROM mqtt_queue WHERE retain=1 AND id NOT IN "
            "(SELECT MAX(id) FROM mqtt_queue WHERE retain=1 GROUP BY topic)"
        )
        return int(cursor.rowcount or 0)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def enqueue(self, topic: str, payload: str | bytes, qos: int = 1, retain: bool = False) -> int:
        body = payload.encode("utf-8") if isinstance(payload, str) else payload
        with self._lock, self._connection:
            if retain:
                self._connection.execute("DELETE FROM mqtt_queue WHERE topic=? AND retain=1", (topic,))
            cursor = self._connection.execute(
                "INSERT INTO mqtt_queue(topic,payload,qos,retain,created_at) VALUES(?,?,?,?,?)",
                (topic, body, int(qos), int(bool(retain)), utc_now()),
            )
            row_id = int(cursor.lastrowid)
            self._enforce_queue_limit_locked()
            return row_id

    def _enforce_queue_limit_locked(self) -> int:
        limit = max(100, int(self.settings.get("max_buffer_messages", 100000)))
        row = self._connection.execute("SELECT COUNT(*) AS total FROM mqtt_queue").fetchone()
        excess = max(0, int(row["total"]) - limit)
        if excess:
            self._connection.execute(
                "DELETE FROM mqtt_queue WHERE id IN (SELECT id FROM mqtt_queue ORDER BY id LIMIT ?)",
                (excess,),
            )
        return excess

    def next_messages(self, limit: int) -> list[dict[str, Any]]:
        now = time.time()
        max_attempts = int(self.settings.get("max_attempts", 0) or 0)
        sql = "SELECT * FROM mqtt_queue ORDER BY id LIMIT ?"
        args: list[Any] = [max(1, int(limit))]
        with self._lock:
            rows = self._connection.execute(sql, args).fetchall()
        ready = []
        for row in rows:
            # Nao ultrapassa uma mensagem antiga em backoff: replay realmente FIFO.
            if float(row["next_attempt"]) > now or (max_attempts > 0 and int(row["attempts"]) >= max_attempts):
                break
            ready.append({**dict(row), "payload": bytes(row["payload"]), "retain": bool(row["retain"])})
        return ready

    def acknowledge(self, row_id: int) -> None:
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM mqtt_queue WHERE id=?", (int(row_id),))

    def retry(self, row_id: int, error: str, delay_seconds: float) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE mqtt_queue SET attempts=attempts+1,next_attempt=?,last_error=? WHERE id=?",
                (time.time() + max(0.1, delay_seconds), str(error)[:1000], int(row_id)),
            )

    def queue_stats(self) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) total,COALESCE(SUM(LENGTH(payload)),0) bytes,"
                "COALESCE(MIN(created_at),'') oldest,COALESCE(MAX(attempts),0) max_attempts FROM mqtt_queue"
            ).fetchone()
        return {
            "messages": int(row["total"]),
            "payload_bytes": int(row["bytes"]),
            "oldest": row["oldest"],
            "max_attempts": int(row["max_attempts"]),
            "database_bytes": self.database_size(),
        }

    def database_size(self) -> int:
        return sum(
            candidate.stat().st_size
            for candidate in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm"))
            if candidate.exists()
        )

    def save_telemetry(self, device_id: str, topic: str, payload: dict[str, Any], quality: int) -> None:
        text = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        sampled_at = str(payload.get("timestamp") or payload.get("date_time") or utc_now())
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO telemetry_latest(device_id,topic,payload,quality,sampled_at,updated_monotonic)
                   VALUES(?,?,?,?,?,?) ON CONFLICT(device_id) DO UPDATE SET
                   topic=excluded.topic,payload=excluded.payload,quality=excluded.quality,
                   sampled_at=excluded.sampled_at,updated_monotonic=excluded.updated_monotonic""",
                (device_id, topic, text, int(quality), sampled_at, time.monotonic()),
            )

    def latest_telemetry(self, limit: int = 2000) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT device_id,topic,payload,quality,sampled_at FROM telemetry_latest ORDER BY device_id LIMIT ?",
                (max(1, min(int(limit), 10000)),),
            ).fetchall()
        result = []
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except json.JSONDecodeError:
                payload = {"_invalid_payload": row["payload"]}
            result.append({**dict(row), "payload": payload})
        return result

    def telemetry_for_device(self, device_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM telemetry_latest WHERE device_id=?", (device_id,)
            ).fetchone()
        if row is None:
            return None
        return {**dict(row), "payload": json.loads(row["payload"])}

    def event(self, level: str, source: str, code: str, message: str, detail: Any = None) -> int:
        try:
            detail_json = json.dumps(detail if detail is not None else {}, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            detail_json = json.dumps({"repr": repr(detail)}, ensure_ascii=False)
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "INSERT INTO events(created_at,level,source,code,message,detail) VALUES(?,?,?,?,?,?)",
                (utc_now(), str(level).upper(), source[:128], code[:128], message[:2000], detail_json),
            )
            return int(cursor.lastrowid)

    def events(self, limit: int = 200, after_id: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM events WHERE id>? ORDER BY id DESC LIMIT ?",
                (int(after_id), max(1, min(int(limit), 2000))),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["detail"] = json.loads(item["detail"])
            except json.JSONDecodeError:
                pass
            result.append(item)
        return result

    def daily_energy(self, device_id: str, field_id: str, local_date: str, total: float) -> float:
        """Atualiza baseline diario de forma transacional e tolera reset do contador."""
        now = utc_now()
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT * FROM daily_energy WHERE device_id=? AND field_id=?", (device_id, field_id)
            ).fetchone()
            if row is None or row["local_date"] != local_date or total < float(row["last_total"]):
                baseline = total
                daily = 0.0
            else:
                baseline = float(row["baseline"])
                daily = max(0.0, total - baseline)
            self._connection.execute(
                """INSERT INTO daily_energy(device_id,field_id,local_date,baseline,last_total,daily_value,updated_at)
                   VALUES(?,?,?,?,?,?,?) ON CONFLICT(device_id,field_id) DO UPDATE SET
                   local_date=excluded.local_date,baseline=excluded.baseline,last_total=excluded.last_total,
                   daily_value=excluded.daily_value,updated_at=excluded.updated_at""",
                (device_id, field_id, local_date, baseline, total, daily, now),
            )
            return daily

    def prune(self) -> dict[str, int]:
        event_days = max(1, int(self.settings.get("event_retention_days", 30)))
        event_cutoff = (datetime.now(UTC) - timedelta(days=event_days)).isoformat()
        queue_days = max(1, int(self.settings.get("retention_days", 30)))
        queue_cutoff = (datetime.now(UTC) - timedelta(days=queue_days)).isoformat()
        with self._lock:
            # O checkpoint precisa ocorrer depois do COMMIT. Se for executado
            # dentro da transacao dos DELETEs, o SQLite responde "table is
            # locked" e pode encerrar a thread supervisora do gateway.
            with self._connection:
                old_events = self._connection.execute("DELETE FROM events WHERE created_at<?", (event_cutoff,)).rowcount
                old_queue = self._connection.execute("DELETE FROM mqtt_queue WHERE created_at<?", (queue_cutoff,)).rowcount
                overflow = self._enforce_queue_limit_locked()
            self._connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
        return {"events": int(old_events), "expired_messages": int(old_queue), "overflow_messages": int(overflow)}

    def health(self) -> dict[str, Any]:
        try:
            with self._lock:
                result = self._connection.execute("PRAGMA quick_check").fetchone()[0]
            stat = os.statvfs(self.path.parent) if hasattr(os, "statvfs") else None
            return {
                "healthy": result == "ok",
                "quick_check": result,
                "free_bytes": stat.f_bavail * stat.f_frsize if stat else None,
                **self.queue_stats(),
            }
        except Exception as exc:
            return {"healthy": False, "error": str(exc)}
