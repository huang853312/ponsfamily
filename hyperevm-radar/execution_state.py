"""Durable execution state; this module makes no alert eligibility decisions."""
import asyncio
from contextlib import contextmanager
import json
import sqlite3
import time
import uuid

import database


@contextmanager
def connection():
    conn = sqlite3.connect(database.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_execution_db():
    with connection() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS contract_processing (
            address TEXT PRIMARY KEY, event_json TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS telegram_outbox (
            notification_key TEXT PRIMARY KEY, message TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at REAL NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0,
            claim_token TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
            sent_at REAL, last_error TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_telegram_outbox_due
            ON telegram_outbox(status, next_attempt_at, lease_until);
        CREATE TABLE IF NOT EXISTS pool_observations (
            factory TEXT NOT NULL, pool TEXT NOT NULL, block_number INTEGER NOT NULL,
            event_json TEXT NOT NULL, validation_status TEXT NOT NULL,
            updated_at REAL NOT NULL, PRIMARY KEY(factory,pool,block_number)
        );
        """)


def record_pool_observation(info, block_number, status):
    with connection() as conn:
        conn.execute('''INSERT INTO pool_observations VALUES(?,?,?,?,?,?)
            ON CONFLICT(factory,pool,block_number) DO UPDATE SET
            validation_status=excluded.validation_status,updated_at=excluded.updated_at''',
                     (info['factory'].lower(), info['pool'].lower(), int(block_number),
                      json.dumps(info, default=str), status, time.time()))


def begin_contract_processing(event, legacy_seen):
    address = event['address'].lower()
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT state FROM contract_processing WHERE address=?', (address,)).fetchone()
        if row and row['state'] == 'completed':
            return False
        if row is None:
            # Existing historical records retain their previous deduplication rule.
            if legacy_seen:
                return False
            conn.execute('INSERT INTO contract_processing(address,event_json,updated_at) VALUES(?,?,?)',
                         (address, json.dumps(event, default=str), time.time()))
        conn.execute('UPDATE contract_processing SET attempts=attempts+1,updated_at=? WHERE address=?',
                     (time.time(), address))
        return True


def complete_contract_processing(address):
    with connection() as conn:
        conn.execute("UPDATE contract_processing SET state='completed',last_error='',updated_at=? WHERE address=?",
                     (time.time(), address.lower()))


def fail_contract_processing(address, error):
    with connection() as conn:
        # Keep only the type: RPC exceptions may embed credential-bearing URLs.
        conn.execute('UPDATE contract_processing SET last_error=?,updated_at=? WHERE address=?',
                     (type(error).__name__, time.time(), address.lower()))


def enqueue_notification(notification_key, message, conn=None):
    if not notification_key or not message:
        raise ValueError('Notification key and message are required')
    if conn is None:
        with connection() as owned:
            return enqueue_notification(notification_key, message, conn=owned)
    return conn.execute('INSERT OR IGNORE INTO telegram_outbox(notification_key,message,created_at) VALUES(?,?,?)',
                        (notification_key, message, time.time())).rowcount == 1


def claim_notification(now=None):
    now = time.time() if now is None else now
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute("""SELECT * FROM telegram_outbox
            WHERE (status='pending' AND next_attempt_at<=?)
               OR (status='sending' AND lease_until<=?)
            ORDER BY created_at, notification_key LIMIT 1""", (now, now)).fetchone()
        if not row:
            return None
        claim = uuid.uuid4().hex
        conn.execute("""UPDATE telegram_outbox SET status='sending',attempts=attempts+1,
            lease_until=?,claim_token=? WHERE notification_key=?""",
                     (now + 120, claim, row['notification_key']))
        return {**dict(row), 'claim_token': claim, 'attempts': row['attempts'] + 1}


async def deliver_pending_once(sender, now=None):
    row = claim_notification(now)
    if row is None:
        return False
    try:
        if await sender(row['message']) is not True:
            raise RuntimeError('Telegram did not acknowledge delivery')
    except asyncio.CancelledError:
        # An interrupted/unknown outcome is reclaimed after the lease expires.
        raise
    except Exception as exc:
        finished = time.time() if now is None else now
        delay = min(300, 5 * 2 ** min(row['attempts'] - 1, 6))
        with connection() as conn:
            conn.execute("""UPDATE telegram_outbox SET status='pending',next_attempt_at=?,
                lease_until=0,last_error=? WHERE notification_key=? AND claim_token=?""",
                         (finished + delay, type(exc).__name__, row['notification_key'], row['claim_token']))
        print('TELEGRAM_RETRY', row['notification_key'], 'attempt=', row['attempts'],
              'delay=', delay, 'error=', type(exc).__name__, flush=True)
    else:
        with connection() as conn:
            conn.execute("""UPDATE telegram_outbox SET status='sent',sent_at=?,lease_until=0,last_error=''
                WHERE notification_key=? AND claim_token=?""",
                         (time.time(), row['notification_key'], row['claim_token']))
        print('TELEGRAM_SENT', row['notification_key'], 'attempt=', row['attempts'], flush=True)
    return True


async def notification_worker(sender):
    while True:
        try:
            active = await deliver_pending_once(sender)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print('TELEGRAM_WORKER_ERROR', type(exc).__name__, flush=True)
            active = False
        await asyncio.sleep(0.1 if active else 1)
