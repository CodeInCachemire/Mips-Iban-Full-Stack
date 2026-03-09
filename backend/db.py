import os
import sqlite3
import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)

# Use absolute path or override via environment variable
PATH_DB = os.getenv("DATABASE_PATH", "/app/data/history.db")

CREATE_TABLE = "CREATE TABLE IF NOT EXISTS conversion_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, direction TEXT NOT NULL, input TEXT NOT NULL, output TEXT NOT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP);"
INSERT_INTO = "INSERT INTO conversion_logs (direction, input, output) VALUES (?,?,?);"
READ_LAST_20 = "SELECT * FROM conversion_logs ORDER BY created_at DESC LIMIT 20"


def init_db() -> None:
    try:
        db_dir = os.path.dirname(PATH_DB)
        os.makedirs(db_dir, exist_ok=True)
        conn = sqlite3.connect(PATH_DB)
        try:
            cursor = conn.cursor()
            cursor.execute(CREATE_TABLE)
            conn.commit()
            logger.info("Database initialized successfully")
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.error(f"Database initialization failed: {e}")
        raise
    except OSError as e:
        logger.error(f"Failed to create data directory: {e}")
        raise


def log_conversion(direction: str, input_value: str, output_value: str) -> None:
    try:
        conn = sqlite3.connect(PATH_DB)
        try:
            cursor = conn.cursor()
            cursor.execute(INSERT_INTO, (direction, input_value, output_value))
            conn.commit()
            logger.info(f"Logged conversion: {direction}")
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.error(f"Failed to log conversion: {e}")
        raise


def read_conversion() -> List[Tuple]:
    try:
        conn = sqlite3.connect(PATH_DB)
        try:
            cursor = conn.cursor()
            cursor.execute(READ_LAST_20)
            data = cursor.fetchall()
            logger.info(f"Retrieved {len(data)} conversion records")
            return data
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.error(f"Failed to read conversions: {e}")
        raise