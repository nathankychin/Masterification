import json
import math
import os
import random
import sqlite3
import urllib.parse
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, flash, redirect, render_template, request, session, url_for, jsonify
from werkzeug.security import check_password_hash, generate_password_hash

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-please-change")
app.permanent_session_lifetime = timedelta(days=30)
app.config.update(
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "0").lower() in {"1", "true", "yes", "on"},
    SESSION_REFRESH_EACH_REQUEST=True,
)

# Logging: stream to stdout so Render captures stack traces and info
import logging
logging.basicConfig(level=logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
app.logger.addHandler(handler)

DATABASE = os.environ.get("DATABASE", "skills.db")
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

# Postgres support (optional)
POSTGRES_AVAILABLE = False
try:
    if DATABASE_URL:
        import psycopg
        from psycopg.rows import dict_row
        POSTGRES_AVAILABLE = True
except Exception:
    POSTGRES_AVAILABLE = False
DEV_CONSOLE_PASSWORD = os.environ.get("DEV_CONSOLE_PASSWORD", "dev-console-2026")
COLLAB_PASSWORD = os.environ.get("COLLAB_PASSWORD", "collab-console-2026")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
APP_NAME = os.environ.get("APP_NAME", "Masterify")
APP_DOMAIN = os.environ.get("APP_DOMAIN", "masterify.app")
app.config["PREFERRED_URL_SCHEME"] = os.environ.get("PREFERRED_URL_SCHEME", "https")


@app.context_processor
def inject_app_context():
    return {"app_name": APP_NAME, "app_domain": APP_DOMAIN}

@app.before_request
def refresh_permanent_session():
    if session.get("user_id"):
        session.permanent = True

@app.after_request
def set_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response

if OPENAI_AVAILABLE and OPENAI_API_KEY:
    client = OpenAI(api_key=OPENAI_API_KEY)
else:
    client = None


def get_db():
    """Return a DB connection wrapper that supports execute(...).

    Uses SQLite when no `DATABASE_URL` is set, otherwise uses Postgres via psycopg.
    """
    class CursorWrapper:
        def __init__(self, cur, pg=False):
            self.cur = cur
            self.pg = pg
            self.lastrowid = None

        def fetchone(self):
            row = self.cur.fetchone()
            if row is None:
                return None
            if self.pg:
                cols = [d.name for d in self.cur.description]
                return dict(zip(cols, row))
            try:
                return dict(row)
            except Exception:
                cols = [d[0] for d in self.cur.description]
                return dict(zip(cols, row))

        def fetchall(self):
            rows = self.cur.fetchall()
            if self.pg:
                cols = [d.name for d in self.cur.description]
                return [dict(zip(cols, r)) for r in rows]
            try:
                return [dict(r) for r in rows]
            except Exception:
                cols = [d[0] for d in self.cur.description]
                return [dict(zip(cols, r)) for r in rows]

    class DBConnWrapper:
        def __init__(self, conn, pg=False):
            self.conn = conn
            self.pg = pg

        def execute(self, sql, params=()):
            cur = self.conn.cursor()
            if self.pg:
                # adapt sqlite-style '?' placeholders to psycopg '%s'
                sql = sql.replace('?', '%s')
                # For Postgres, ensure we return id for INSERTs when caller expects it
                needs_returning = sql.strip().upper().startswith("INSERT") and "RETURNING" not in sql.upper()
                if needs_returning:
                    sql = sql.rstrip(';') + " RETURNING id"
                cur.execute(sql, params)
                wrapper = CursorWrapper(cur, pg=True)
                # If an INSERT with RETURNING, capture lastrowid
                if wrapper.cur.description and wrapper.cur.rowcount >= 0:
                    try:
                        first = cur.fetchone()
                        if first:
                            cols = [d.name for d in cur.description]
                            if 'id' in cols:
                                wrapper.lastrowid = first[cols.index('id')]
                                # move cursor state back for fetchone/fetchall if needed
                    except Exception:
                        pass
                return wrapper
            else:
                cur.execute(sql, params)
                wrapper = CursorWrapper(cur, pg=False)
                # sqlite3 cursor has lastrowid attribute
                try:
                    wrapper.lastrowid = cur.lastrowid
                except Exception:
                    wrapper.lastrowid = None
                return wrapper

        def commit(self):
            try:
                self.conn.commit()
            except Exception:
                pass

        def close(self):
            try:
                self.conn.close()
            except Exception:
                pass

    if POSTGRES_AVAILABLE:
        conn = psycopg.connect(DATABASE_URL)
        return DBConnWrapper(conn, pg=True)
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return DBConnWrapper(conn, pg=False)


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok", "app": APP_NAME, "domain": APP_DOMAIN})


def get_practice_mode_data(skill_name: str, category: str = None):
    name = (skill_name or "").lower()
    category_name = (category or "").lower()
    if any(keyword in category_name for keyword in ["language", "linguistic", "literature", "english", "foreign"]) or any(keyword in name for keyword in ["english", "spanish", "french", "german", "chinese", "japanese", "korean", "language", "vocabulary", "literature"]):
        # For languages, generate a fresh phrase or short passage so exercises vary.
        phrase = generate_creative_phrase(skill_name or category or "language")
        return {
            "type": "speech",
            "title": "Answer aloud",
            "prompt": f"Explain this short phrase or passage in your own words: '{phrase}'",
            "instruction": "Toggle the microphone and speak clearly as if you were answering an oral or speaking exam question.",
            "target": "Answer",
            "helper": "Focus on clarity, structure, vocabulary and pronunciation when you speak.",
        }
    if any(keyword in category_name for keyword in ["math", "mathematics", "physics", "chemistry", "biology", "science"]) or any(keyword in name for keyword in ["math", "mathematics", "physics", "chemistry", "biology", "science"]):
        return {
            "type": "text",
            "title": "Solve the problem",
            "prompt": "Work through this exam-style question and explain your reasoning step by step.",
            "instruction": "Type your full method and final answer in the space below so you can review your logic.",
            "target": "Method",
            "helper": "Show each stage clearly, especially where marks are awarded for method.",
        }
    return {
        "type": "text",
        "title": "Respond to the exam prompt",
        "prompt": f"Explain how you would approach {skill_name or 'this study topic'} in an exam-style response.",
        "instruction": "Type your answer in the space below and review the feedback once you submit.",
        "target": "Response",
        "helper": "Keep your explanation focused, structured, and suitable for an IGCSE, O-level, or A-level context.",
    }


def create_user(conn, username, password, role="user", exam_board=""):
    existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if existing:
        return None
    conn.execute(
        "INSERT INTO users (username, password_hash, role, created_at, exam_board) VALUES (?, ?, ?, ?, ?)",
        (username, generate_password_hash(password), role, datetime.utcnow().isoformat(), exam_board),
    )
    return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def ensure_user_exam_board_column(conn):
    try:
        rows = conn.execute("PRAGMA table_info(users)").fetchall()
        existing = []
        for row in rows:
            if isinstance(row, dict):
                existing.append(row.get('name'))
            else:
                # fallback to index-based row
                existing.append(row[1] if len(row) > 1 else None)
        if "exam_board" not in existing:
            conn.execute("ALTER TABLE users ADD COLUMN exam_board TEXT DEFAULT ''")
    except Exception:
        # For Postgres or other DBs, check information_schema
        res = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='exam_board'").fetchone()
        if not res:
            conn.execute("ALTER TABLE users ADD COLUMN exam_board TEXT DEFAULT ''")


def ensure_practice_logs_columns(conn):
    try:
        rows = conn.execute("PRAGMA table_info(practice_logs)").fetchall()
        existing = []
        for row in rows:
            if isinstance(row, dict):
                existing.append(row.get('name'))
            else:
                existing.append(row[1] if len(row) > 1 else None)
        if "response_text" not in existing:
            conn.execute("ALTER TABLE practice_logs ADD COLUMN response_text TEXT DEFAULT ''")
        if "feedback_summary" not in existing:
            conn.execute("ALTER TABLE practice_logs ADD COLUMN feedback_summary TEXT DEFAULT ''")
        if "feedback_rubric" not in existing:
            conn.execute("ALTER TABLE practice_logs ADD COLUMN feedback_rubric TEXT DEFAULT ''")
    except Exception:
        # Postgres information_schema check
        cols = [c['column_name'] for c in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name='practice_logs'").fetchall()]
        if 'response_text' not in cols:
            conn.execute("ALTER TABLE practice_logs ADD COLUMN response_text TEXT DEFAULT ''")
        if 'feedback_summary' not in cols:
            conn.execute("ALTER TABLE practice_logs ADD COLUMN feedback_summary TEXT DEFAULT ''")
        if 'feedback_rubric' not in cols:
            conn.execute("ALTER TABLE practice_logs ADD COLUMN feedback_rubric TEXT DEFAULT ''")


def init_db(force=False):
    conn = get_db()
    if force:
        try:
            conn.execute("DROP TABLE IF EXISTS practice_logs")
            conn.execute("DROP TABLE IF EXISTS skills")
            conn.execute("DROP TABLE IF EXISTS users")
        except Exception:
            pass

    # Use DB-specific CREATE statements for Postgres vs SQLite
    if POSTGRES_AVAILABLE:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL,
                exam_board TEXT DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS skills (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                context TEXT NOT NULL,
                importance REAL NOT NULL,
                usage_frequency REAL NOT NULL,
                readiness REAL NOT NULL,
                days_since_practice INTEGER NOT NULL,
                reminder_days INTEGER NOT NULL,
                category TEXT DEFAULT 'Other',
                last_practiced_at TEXT,
                next_review_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS practice_logs (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                skill_id INTEGER NOT NULL,
                score REAL NOT NULL,
                readiness_before REAL NOT NULL,
                readiness_after REAL NOT NULL,
                practiced_at TEXT NOT NULL,
                response_text TEXT DEFAULT '',
                feedback_summary TEXT DEFAULT '',
                feedback_rubric TEXT DEFAULT '',
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(skill_id) REFERENCES skills(id)
            )
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL,
                exam_board TEXT DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS skills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                context TEXT NOT NULL,
                importance REAL NOT NULL,
                usage_frequency REAL NOT NULL,
                readiness REAL NOT NULL,
                days_since_practice INTEGER NOT NULL,
                reminder_days INTEGER NOT NULL,
                category TEXT DEFAULT 'Other',
                last_practiced_at TEXT,
                next_review_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS practice_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                skill_id INTEGER NOT NULL,
                score REAL NOT NULL,
                readiness_before REAL NOT NULL,
                readiness_after REAL NOT NULL,
                practiced_at TEXT NOT NULL,
                response_text TEXT DEFAULT '',
                feedback_summary TEXT DEFAULT '',
                feedback_rubric TEXT DEFAULT '',
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(skill_id) REFERENCES skills(id)
            )
            """
        )

    ensure_user_exam_board_column(conn)
    ensure_practice_logs_columns(conn)

    create_user(conn, "admin", DEV_CONSOLE_PASSWORD, "admin")
    create_user(conn, "collab", COLLAB_PASSWORD, "admin")
    try:
        conn.commit()
    except Exception:
        pass
    try:
        conn.close()
    except Exception:
        pass


force_reset = os.environ.get("RESET_DB", "").lower() in {"1", "true", "yes", "on"}
init_db(force=force_reset)


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(user) if user else None


def clamp(value, low, high):
    return max(low, min(high, value))


def calculate_risk_weight(skill):
    importance_factor = skill["importance"] / 10.0
    usage_factor = (10.0 - skill["usage_frequency"]) / 10.0
    return 1.0 + (importance_factor * 0.8) + (usage_factor * 0.7)


def projected_readiness(skill, days_since_last_practice):
    risk = calculate_risk_weight(skill)
    decay_rate = 0.05 * risk
    projected = skill["readiness"] * math.exp(-decay_rate * days_since_last_practice)
    return clamp(projected, 0.0, 100.0)


def get_readiness_gradient(readiness):
    if readiness < 40:
        return "linear-gradient(135deg, #ef4444 0%, #fb923c 100%)"
    if readiness < 70:
        return "linear-gradient(135deg, #f59e0b 0%, #fde68a 100%)"
    return "linear-gradient(135deg, #22c55e 0%, #86efac 100%)"


def get_priority_label(skill):
    if skill["importance"] >= 8:
        return "High priority"
    if skill["importance"] >= 5:
        return "Medium priority"
    return "Steady"


def is_overdue(skill):
    if not skill["next_review_at"]:
        return False
    try:
        next_review = datetime.fromisoformat(skill["next_review_at"])
    except ValueError:
        return False
    return datetime.utcnow() > next_review and skill["readiness"] < 70


def get_next_review_text(skill):
    if skill["next_review_at"]:
        try:
            due = datetime.fromisoformat(skill["next_review_at"])
            delta = (due - datetime.utcnow()).days
            if delta <= 0:
                return "Due now"
            return f"Due in {delta} day{'s' if delta != 1 else ''}"
        except ValueError:
            pass
    return "No review scheduled"


def categorize_skill_ai(skill_name: str, skill_context: str) -> str:
    """Use AI to categorize skill into a group."""
    if not client:
        return get_skill_category_fallback(skill_name)
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": "Categorize the skill into ONE: Languages, Musical, Technical, Safety, Medical, Soft Skills, or Other. Respond with only the category.",
                },
                {
                    "role": "user",
                    "content": f"Skill: {skill_name}. Context: {skill_context}.",
                },
            ],
            temperature=0.3,
            max_tokens=20,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"AI categorization failed: {e}")
        return get_skill_category_fallback(skill_name)


def get_skill_category_fallback(skill_name: str) -> str:
    """Fallback categorization."""
    name_lower = skill_name.lower()
    if any(word in name_lower for word in ["english", "literature", "spanish", "french", "german", "chinese", "language", "vocabulary", "essay", "poetry"]):
        return "Languages"
    if any(word in name_lower for word in ["math", "mathematics", "algebra", "calculus", "geometry", "physics", "chemistry", "biology", "science"]):
        return "Science & Maths"
    if any(word in name_lower for word in ["history", "geography", "business", "economics", "accounting", "economy"]):
        return "Humanities"
    if any(word in name_lower for word in ["computer", "programming", "coding", "python", "java", "sql"]):
        return "Technical"
    return "Other"


def board_offers_topic(exam_board: str, skill_name: str, category: str = None) -> bool:
    """Rudimentary check whether a study board typically offers this topic.

    Returns False for clearly external/engineering/tertiary topics when the board is an exam-focused school board.
    """
    if not exam_board:
        return True
    b = (exam_board or "").strip().upper()
    name = (skill_name or "").lower()
    cat = (category or "").lower()
    if b in {"IGCSE", "GCSE", "O-LEVEL"}:
        # these school boards rarely include engineering/tertiary vocational topics
        if any(k in name for k in ["electrical", "engineering", "circuit", "power systems"]) or any(k in cat for k in ["technical", "engineering"]):
            return False
    return True


def generate_question_ai(skill_name: str, skill_context: str, category: str, exam_board: str) -> str:
    """Generate exam-style questions for a specific topic."""
    # Produce two distinct questions: one testing core understanding, one testing application/analysis.
    prompt_text = (
        f"You are an exam question writer for {exam_board} preparation. "
        f"Create TWO distinct, high-quality questions for the exact topic '{skill_name}'. "
        f"Question 1: test core understanding or the main concept (short answer or structured response). "
        f"Question 2: test application, analysis, or an exam-style scenario related to the topic. "
        f"Use the study topic description and context: {skill_context}. Make the wording natural for the subject; if the topic is a language, make one question focus on meaning/usage and the other on translation/production. "
        f"Return only the two question texts, each labeled '1.' and '2.' on separate lines.")
    if not client:
        return generate_question_fallback(skill_name, skill_context, exam_board)
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert exam question writer using a large language model. Generate high-quality, unique questions for a specific topic.",
                },
                {
                    "role": "user",
                    "content": prompt_text,
                },
            ],
            temperature=0.8,
            max_tokens=220,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"AI question generation failed: {e}")
        return generate_question_fallback(skill_name, skill_context, exam_board)


def generate_question_fallback(skill_name: str, skill_context: str, exam_board: str) -> str:
    """Fallback question generation."""
    # Provide clearer, more distinct fallback questions. Adapt wording slightly for language topics.
    lname = (skill_name or "").lower()
    if any(k in lname for k in ["english", "spanish", "french", "german", "chinese", "japanese", "language"]):
        return (
            f"1. In a short paragraph, explain the meaning or primary usage of the following topic: {skill_name}.\n"
            f"2. Produce a short example (sentence or short paragraph) that demonstrates correct use of the topic, as an exam answer would show."
        )
    return (
        f"1. Explain the key concept within '{skill_name}' that an {exam_board} marker would expect in a concise answer.\n"
        f"2. Give a worked example or application of '{skill_name}' showing how you would structure an exam-style response to earn marks."
    )


def generate_creative_phrase(topic_hint: str = None) -> str:
    """Generate a short phrase or passage for language practice. Use the AI client if available, otherwise pick from a small fallback set."""
    fallbacks = [
        "The smallest act of kindness is worth more than the grandest intention.",
        "A single conversation across the table with a wise person is worth a month's study of books.",
        "When in doubt, take the next small step.",
        "The river cuts through rock not because of its power but its persistence.",
        "Learning a language opens a door to someone else's world."
    ]
    if client:
        try:
            prompt = f"Provide one short, natural-sounding phrase or sentence suitable for a language speaking or translation exercise. Keep it between 6 and 18 words. Context hint: {topic_hint or 'general'}"
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are a creative phrase generator for language practice."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.9,
                max_tokens=60,
            )
            text = response.choices[0].message.content.strip().split('\n')[0]
            return text.strip(' "')
        except Exception:
            pass
    return random.choice(fallbacks)


@app.route("/api/phrase")
def api_phrase():
    topic = request.args.get("topic") or request.args.get("skill") or "general"
    phrase = generate_creative_phrase(topic)
    return jsonify({"phrase": phrase})


def get_board_scoring_rules(exam_board: str) -> dict:
    """Return scoring weights for simple rubric components per board."""
    b = (exam_board or "").strip().upper()
    # Base weights (how important each component is)
    base = {
        "keywords": 1.0,
        "reasoning": 1.0,
        "structure": 1.0,
        "length": 0.8,
        "markers": 0.8,
        "sentences": 0.8,
    }
    if b in {"IGCSE", "GCSE", "O-LEVEL"}:
        base.update({"keywords": 1.1, "reasoning": 1.1, "structure": 1.0, "length": 0.7})
    elif b.startswith("A-LEVEL") or b == "A-LEVEL":
        base.update({"keywords": 1.2, "reasoning": 1.3, "structure": 1.1, "length": 0.8})
    elif b == "EXTERNAL":
        base.update({"keywords": 0.9, "reasoning": 0.9, "length": 1.0})
    return base


def generate_guidance_ai(skill_name: str, user_response: str, score: int, skill_context: str = "") -> str:
    """Generate personalized AI guidance."""
    if not client:
        return build_feedback_rubric(skill_name, user_response, score)["summary"]
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": f"You are a tutor for {skill_name}. Analyze the response and provide concise feedback with: (1) what was done well, (2) where marks were likely deducted, and (3) one practical next step. Use the topic description when relevant. Keep it 4-5 sentences.",
                },
                {
                    "role": "user",
                    "content": f"Topic: {skill_name}\nTopic description: {skill_context}\nMy response: {user_response}\nMy score: {score}/100",
                },
            ],
            temperature=0.6,
            max_tokens=220,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"AI guidance failed: {e}")
        return build_feedback_rubric(skill_name, user_response, score)["summary"]


def expected_keywords(skill_name):
    name = skill_name.lower()
    if "cpr" in name:
        return ["check", "call", "compressions", "airway", "breathing"]
    if "sql" in name:
        return ["join", "select", "where", "group", "from"]
    if "spanish" in name or "language" in name:
        return ["hola", "gracias", "como", "estoy", "puedo"]
    if "safety" in name or "lockout" in name:
        return ["isolate", "verify", "lock", "safe", "hazard"]
    return ["first", "steps", "verify", "decision"]


def evaluate_response(skill_name, response, exam_board=None):
    """Evaluate a free-text response and return a 0-100 score.

    `exam_board` can slightly adjust strictness (IGCSE/GCSE/O-LEVEL stricter; A-LEVEL stricter still).
    """
    lowered = (response or "").lower()
    score = 0
    keywords = expected_keywords(skill_name)
    for keyword in keywords:
        if keyword in lowered:
            score += 1

    # Reward reasonable length and structure
    if len(response.strip()) >= 80:
        score += 2
    if len(response.strip()) >= 140:
        score += 1
    if "first" in lowered and ("steps" in lowered or "step" in lowered):
        score += 1
    if "because" in lowered or "why" in lowered:
        score += 1
    if any(marker in lowered for marker in ["for example", "such as", "however", "therefore", "because", "this shows", "in conclusion"]):
        score += 1
    if lowered.count(".") >= 2:
        score += 1

    # Base score from keyword hits
    kw_hits = sum(1 for k in keywords if k in lowered)
    kw_total = max(1, len(keywords))

    # Feature sub-scores (out of 100 before weighting)
    kw_score = (kw_hits / kw_total) * 40.0
    reasoning_score = 20.0 if any(marker in lowered for marker in ["because", "why", "therefore", "for example"]) else 0.0
    structure_score = 15.0 if ("first" in lowered and ("steps" in lowered or "step" in lowered)) else 0.0
    length_score = 10.0 if len(response.strip()) >= 80 else 0.0
    extra_length = 5.0 if len(response.strip()) >= 140 else 0.0
    sentence_score = 10.0 if lowered.count(".") >= 2 else 0.0

    rules = get_board_scoring_rules(exam_board or "")
    total_raw = (
        kw_score * rules.get("keywords", 1.0)
        + reasoning_score * rules.get("reasoning", 1.0)
        + structure_score * rules.get("structure", 1.0)
        + (length_score + extra_length) * rules.get("length", 1.0)
        + sentence_score * rules.get("sentences", 1.0)
    )

    # Normalize to 0-100
    normalized = clamp(total_raw, 0.0, 100.0)
    if normalized < 25 and len(response.strip()) >= 40:
        return 35.0
    return round(normalized, 1)


def build_feedback_rubric(skill_name, response, score):
    lowered = (response or "").lower()
    strengths = []
    improvements = []
    deductions = []

    if score >= 80:
        summary = f"Strong readiness for {skill_name}. Your answer was clear and showed solid recall of the key ideas."
    elif score >= 60:
        summary = f"Good progress for {skill_name}. Your answer showed useful understanding, but a few key points could be made clearer."
    else:
        summary = f"Moderate readiness for {skill_name}. The response showed some understanding, but key detail and structure were missing."

    if len((response or "").strip()) >= 60:
        strengths.append("Included enough detail to show reasoning beyond a one-line reply.")
    else:
        improvements.append("Add more explanation so answers are complete, not brief notes.")
        deductions.append("Insufficient detail")

    if any(marker in lowered for marker in ["because", "why", "therefore", "for example"]):
        strengths.append("Reasoning and linking of ideas was present.")
    else:
        improvements.append("Explicitly explain why points matter to show examiner your reasoning.")
        deductions.append("Missing reasoning")

    if "first" in lowered and ("steps" in lowered or "step" in lowered):
        strengths.append("Logical sequence or steps make the answer easy to follow.")
    else:
        improvements.append("Structure the response with clear steps or paragraphs.")
        deductions.append("Weak structure")

    keyword_hits = [k for k in expected_keywords(skill_name) if k in lowered]
    if keyword_hits:
        strengths.append(f"Used topic-specific terms: {', '.join(keyword_hits[:4])}.")
    else:
        improvements.append("Include exam vocabulary or key topic terms to earn content marks.")
        deductions.append("Missing key terminology")

    mark_deducted = round(max(0.0, 100.0 - score), 1)
    human_readable = {
        "summary": summary,
        "what_went_well": strengths[:3],
        "where_marks_were_deducted": deductions[:4] if deductions else ["A few details could be strengthened."],
        "what_to_improve": improvements[:5],
        "score": round(score, 1),
        "mark_deducted_pct": mark_deducted,
    }

    return human_readable


def is_nonsensical_topic(topic: str) -> bool:
    normalized = topic.strip().lower()
    broad_terms = ["biology", "chemistry", "physics", "math", "mathematics", "history", "geography", "science", "english", "computer science", "subject"]
    if any(normalized == term or normalized == f"{term}" for term in broad_terms):
        return True
    if ":" not in normalized and " " in normalized and len(normalized.split()) <= 2:
        return True
    return False


def feedback_for(skill_name, score):
    if score >= 80:
        return "Strong readiness. The core actions were clear and timely."
    if score >= 50:
        return "Moderate readiness. The response captured the main idea, but some key steps were missing."
    return "Low readiness. The response was too vague and would likely slow down recall under pressure."


def login_required(route):
    @wraps(route)
    def wrapper(*args, **kwargs):
        if not current_user():
            flash("Please sign in to continue.")
            return redirect(url_for("login"))
        return route(*args, **kwargs)
    return wrapper


@app.route("/")
def home():
    if current_user():
        return redirect(url_for("index"))
    return render_template("auth.html", mode="login")


@app.route("/dashboard")
@login_required
def index():
    user = current_user()
    conn = get_db()
    skills = conn.execute(
        "SELECT * FROM skills WHERE user_id = ? ORDER BY importance DESC, readiness ASC",
        (user["id"],),
    ).fetchall()
    practice_logs = conn.execute(
        """
        SELECT practice_logs.*, skills.name AS skill_name
        FROM practice_logs
        JOIN skills ON skills.id = practice_logs.skill_id
        WHERE practice_logs.user_id = ?
        ORDER BY practice_logs.practiced_at DESC
        LIMIT 6
        """,
        (user["id"],),
    ).fetchall()

    formatted_skills = []
    for skill in skills:
        skill_data = dict(skill)
        skill_data["current_readiness"] = round(projected_readiness(skill_data, skill_data["days_since_practice"]), 1)
        skill_data["priority_label"] = get_priority_label(skill_data)
        skill_data["next_review_text"] = get_next_review_text(skill_data)
        skill_data["is_overdue"] = is_overdue(skill_data)
        skill_data["gradient"] = get_readiness_gradient(skill_data["current_readiness"])
        formatted_skills.append(skill_data)

    total_practices = conn.execute("SELECT COUNT(*) AS count FROM practice_logs WHERE user_id = ?", (user["id"],)).fetchone()["count"]
    avg_readiness = round(sum(skill["current_readiness"] for skill in formatted_skills) / len(formatted_skills), 1) if formatted_skills else 0.0
    overdue_count = sum(1 for skill in formatted_skills if skill["is_overdue"])

    conn.close()
    return render_template(
        "index.html",
        skills=formatted_skills,
        stats={
            "total_skills": len(formatted_skills),
            "total_practices": total_practices,
            "avg_readiness": avg_readiness,
            "overdue_count": overdue_count,
        },
        recent_sessions=practice_logs,
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        exam_board = request.form.get("exam_board", "IGCSE").strip() or "IGCSE"
        if not username or not password or not exam_board:
            flash("Please provide a username, password, and exam board.")
            return redirect(url_for("register"))

        conn = get_db()
        existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if existing:
            conn.close()
            flash("That username is already taken.")
            return redirect(url_for("register"))

        user = create_user(conn, username, password, "user", exam_board)
        conn.commit()
        conn.close()
        session["user_id"] = user["id"]
        session["role"] = user["role"]
        session.permanent = True
        flash("Account created successfully. Your device will stay signed in for 30 days.")
        return redirect(url_for("index"))

    return render_template("auth.html", mode="register")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            remember_device = request.form.get("remember_device", "on") == "on"
            session.permanent = remember_device
            if remember_device:
                flash("Welcome back. This device will stay signed in for 30 days.")
            else:
                flash("Welcome back. This session will expire when you close your browser.")
            return redirect(url_for("index"))

        flash("Invalid username or password.")
        return redirect(url_for("login"))

    return render_template("auth.html", mode="login")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.")
    return redirect(url_for("login"))


@app.route("/skills", methods=["POST"])
@login_required
def add_skill():
    user = current_user()
    name = request.form.get("name", "").strip()
    context = request.form.get("context", "").strip()
    importance = float(request.form.get("importance", 5))
    usage_frequency = float(request.form.get("usage_frequency", 3))

    if not name or not context:
        flash("Please provide both a topic and context.")
        return redirect(url_for("index"))

    if is_nonsensical_topic(name):
        flash("That looks too broad or unclear. Try a specific exam topic like 'Biology: Transportation in Plants'.")

    selected_category = request.form.get("category", "").strip()
    category = selected_category or categorize_skill_ai(name, context)
    reminder_days = max(3, int(round(10 - (importance / 2) + (10 - usage_frequency) / 3)))
    now = datetime.utcnow()
    conn = get_db()
    conn.execute(
        """
        INSERT INTO skills (
            user_id, name, context, importance, usage_frequency, readiness,
            days_since_practice, reminder_days, category, last_practiced_at, next_review_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user["id"],
            name,
            context,
            importance,
            usage_frequency,
            80.0,
            0,
            reminder_days,
            category,
            now.isoformat(),
            (now + timedelta(days=reminder_days)).isoformat(),
            now.isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    flash(f"Skill '{name}' saved successfully (Category: {category}).")
    return redirect(url_for("index"))


@app.route("/practice/<int:skill_id>", methods=["GET", "POST"])
@login_required
def practice(skill_id):
    user = current_user()
    conn = get_db()
    skill = conn.execute("SELECT * FROM skills WHERE id = ? AND user_id = ?", (skill_id, user["id"])).fetchone()

    if not skill:
        conn.close()
        flash("Topic not found.")
        return redirect(url_for("index"))

    if request.method == "POST":
        response = request.form.get("response", "")
        exam_board = current_user().get("exam_board", "IGCSE")
        # If the user's selected board doesn't typically offer this topic, flag as external
        if not board_offers_topic(exam_board, skill["name"], skill.get("category")):
            eval_board = "EXTERNAL"
        else:
            eval_board = exam_board
        score = evaluate_response(skill["name"], response, exam_board=eval_board)
        before = projected_readiness(dict(skill), skill["days_since_practice"])
        after = (before * 0.6) + (score * 0.4)

        now = datetime.utcnow()
        reminder_days = max(2, int(round((20 - score / 5) / max(1.0, calculate_risk_weight(dict(skill))))))
        next_review = now + timedelta(days=reminder_days)

        rubric = build_feedback_rubric(skill["name"], response, score)
        guidance = generate_guidance_ai(skill["name"], response, score, skill["context"])

        conn.execute(
            """
            UPDATE skills
            SET readiness = ?, days_since_practice = 0, reminder_days = ?, last_practiced_at = ?, next_review_at = ?
            WHERE id = ?
            """,
            (clamp(after, 0.0, 100.0), reminder_days, now.isoformat(), next_review.isoformat(), skill_id),
        )
        cur = conn.execute(
            "INSERT INTO practice_logs (user_id, skill_id, score, readiness_before, readiness_after, practiced_at, response_text, feedback_summary, feedback_rubric) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user["id"], skill_id, score, before, after, now.isoformat(), response, guidance, json.dumps(rubric)),
        )
        log_id = cur.lastrowid if hasattr(cur, "lastrowid") else conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()

        # Redirect to a dedicated revision/feedback page so flashes don't overlap on the dashboard
        return redirect(url_for("revision_detail", log_id=log_id))

    exam_board = current_user().get("exam_board", "IGCSE")
    questions = generate_question_ai(skill["name"], skill["context"], skill["category"] or "Other", exam_board)
    practice_mode = get_practice_mode_data(skill["name"], skill["category"] or "Other")
    conn.close()
    return render_template(
        "practice.html",
        skill=dict(skill),
        questions=questions,
        practice_mode=practice_mode,
        exam_board=exam_board,
    )


@app.route("/revision/<int:log_id>")
@login_required
def revision_detail(log_id):
    user = current_user()
    conn = get_db()
    log_entry = conn.execute(
        """
        SELECT practice_logs.*, skills.name AS skill_name, skills.context AS skill_context, skills.category AS skill_category
        FROM practice_logs
        JOIN skills ON skills.id = practice_logs.skill_id
        WHERE practice_logs.id = ? AND practice_logs.user_id = ?
        """,
        (log_id, user["id"]),
    ).fetchone()
    conn.close()

    if not log_entry:
        flash("Revision entry not found.")
        return redirect(url_for("index"))

    rubric = {}
    try:
        rubric = json.loads(log_entry["feedback_rubric"] or "{}") if log_entry["feedback_rubric"] else {}
    except json.JSONDecodeError:
        rubric = {}

    return render_template("revision_detail.html", practice_log=dict(log_entry), rubric=rubric)


@app.route("/dev-console", methods=["GET", "POST"])
def dev_console():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        conn.close()
        if user and user["role"] == "admin" and check_password_hash(user["password_hash"], password):
            session["admin_access"] = True
            flash("Admin access granted.")
            return redirect(url_for("dev_console"))
        flash("Invalid admin credentials.")
        return redirect(url_for("dev_console"))

    if session.get("admin_access"):
        conn = get_db()
        total_users = conn.execute("SELECT COUNT(*) AS count FROM users WHERE role = 'user'").fetchone()["count"]
        total_skills = conn.execute("SELECT COUNT(*) AS count FROM skills").fetchone()["count"]
        total_practices = conn.execute("SELECT COUNT(*) AS count FROM practice_logs").fetchone()["count"]
        avg_ready = conn.execute("SELECT AVG(readiness) AS avg FROM skills").fetchone()["avg"] or 0.0
        top_skills = conn.execute(
            "SELECT name, COUNT(*) AS sessions FROM practice_logs JOIN skills ON skills.id = practice_logs.skill_id GROUP BY skill_id ORDER BY sessions DESC LIMIT 5"
        ).fetchall()
        readiness_buckets = conn.execute(
            "SELECT CASE WHEN readiness < 40 THEN 'red' WHEN readiness < 70 THEN 'yellow' ELSE 'green' END AS bucket, COUNT(*) AS count FROM skills GROUP BY bucket"
        ).fetchall()
        categories = conn.execute(
            "SELECT category, COUNT(*) AS count FROM skills GROUP BY category ORDER BY count DESC"
        ).fetchall()
        conn.close()
        return render_template(
            "dev_console.html",
            stats={
                "total_users": total_users,
                "total_skills": total_skills,
                "total_practices": total_practices,
                "avg_ready": round(avg_ready, 1),
                "top_skills": top_skills,
                "readiness_buckets": readiness_buckets,
                "categories": categories,
                "ai_enabled": client is not None,
            },
        )

    return render_template("dev_console.html", stats=None)


@app.route("/dev-console/logout")
def dev_console_logout():
    session.pop("admin_access", None)
    flash("Admin session closed.")
    return redirect(url_for("dev_console"))


# Global error handler to ensure tracebacks appear in Render logs and return a simple message.
@app.errorhandler(Exception)
def handle_exception(e):
    import traceback
    tb = traceback.format_exc()
    app.logger.error("Uncaught exception:\n%s", tb)
    # Re-raise the exception in debug mode so Flask shows the interactive debugger if enabled
    if os.environ.get("FLASK_DEBUG", "0").lower() in {"1", "true", "yes", "on"}:
        raise
    return ("Internal Server Error\nThe server encountered an internal error and was unable to complete your request.", 500)

if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "0").lower() in {"1", "true", "yes", "on"}
    app.run(host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", 5000)), debug=debug_mode, use_reloader=False)
