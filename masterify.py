import json
import math
import os
import random
import sqlite3
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

DATABASE = "skills.db"
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
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok", "app": APP_NAME, "domain": APP_DOMAIN})


def get_practice_mode_data(skill_name: str, category: str = None):
    name = (skill_name or "").lower()
    category_name = (category or "").lower()
    if any(keyword in category_name for keyword in ["language", "linguistic", "literature", "english", "foreign"]) or any(keyword in name for keyword in ["english", "spanish", "french", "german", "chinese", "japanese", "korean", "language", "vocabulary", "literature"]):
        return {
            "type": "speech",
            "title": "Answer aloud",
            "prompt": "Explain this quote or passage in your own words: 'The only way out is through.'",
            "instruction": "Toggle the microphone and speak clearly as if you were answering an oral or speaking exam question.",
            "target": "Answer",
            "helper": "Focus on clarity, structure, and key evidence when you speak.",
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
    existing = [row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "exam_board" not in existing:
        conn.execute("ALTER TABLE users ADD COLUMN exam_board TEXT DEFAULT ''")


def ensure_practice_logs_columns(conn):
    existing = [row[1] for row in conn.execute("PRAGMA table_info(practice_logs)").fetchall()]
    if "response_text" not in existing:
        conn.execute("ALTER TABLE practice_logs ADD COLUMN response_text TEXT DEFAULT ''")
    if "feedback_summary" not in existing:
        conn.execute("ALTER TABLE practice_logs ADD COLUMN feedback_summary TEXT DEFAULT ''")
    if "feedback_rubric" not in existing:
        conn.execute("ALTER TABLE practice_logs ADD COLUMN feedback_rubric TEXT DEFAULT ''")


def init_db(force=False):
    conn = get_db()
    if force:
        conn.execute("DROP TABLE IF EXISTS practice_logs")
        conn.execute("DROP TABLE IF EXISTS skills")
        conn.execute("DROP TABLE IF EXISTS users")

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
    ensure_user_exam_board_column(conn)
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
    ensure_practice_logs_columns(conn)

    create_user(conn, "admin", DEV_CONSOLE_PASSWORD, "admin")
    create_user(conn, "collab", COLLAB_PASSWORD, "admin")
    conn.commit()
    conn.close()


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


def generate_question_ai(skill_name: str, skill_context: str, category: str, exam_board: str) -> str:
    """Generate exam-style questions for a specific topic."""
    prompt_text = (
        f"You are an exam question writer for {exam_board} preparation. "
        f"Create 2 unique, topic-specific questions for the exact topic '{skill_name}'. "
        f"Use the study topic description and context: {skill_context}. "
        f"Tailor the questions and scenarios to the topic's purpose so they feel relevant to the learner's needs, not generic. "
        f"These questions should be appropriate for the stated exam level and should focus on the exact topic rather than the whole subject. "
        f"Return only the two question texts, each on its own line."
    )
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
    return (
        f"1. Explain the main concept within {skill_name} that would be tested by a {exam_board} question.\n"
        f"2. Describe a specific application of {skill_name} or the process it covers in a way an exam answer would show."
    )


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


def evaluate_response(skill_name, response):
    lowered = response.lower()
    score = 0
    for keyword in expected_keywords(skill_name):
        if keyword in lowered:
            score += 1

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

    base_score = clamp(score * 12.0, 0.0, 100.0)
    if base_score < 25 and len(response.strip()) >= 40:
        return 35.0
    return base_score


def build_feedback_rubric(skill_name, response, score):
    lowered = (response or "").lower()
    strengths = []
    improvements = []

    if score >= 80:
        summary = f"Strong readiness for {skill_name}. Your answer was clear and showed solid recall of the key ideas."
    elif score >= 60:
        summary = f"Good progress for {skill_name}. Your answer showed useful understanding, but a few key points could be made clearer."
    else:
        summary = f"Moderate readiness for {skill_name}. The response showed some understanding, but key detail and structure were missing."

    if len((response or "").strip()) >= 60:
        strengths.append("Your answer included enough detail to show some reasoning rather than a one-line response.")
    else:
        improvements.append("Add more explanation so the answer feels complete rather than rushed.")

    if any(marker in lowered for marker in ["because", "why", "therefore", "for example"]):
        strengths.append("You explained some reasoning and connected ideas, which helps exam-style answers sound thoughtful.")
    else:
        improvements.append("Explain why each step or point matters so the examiner can see your reasoning.")

    if "first" in lowered and ("steps" in lowered or "step" in lowered):
        strengths.append("You outlined a logical order, which makes the answer easier to follow.")
    else:
        improvements.append("Structure the response with a clear sequence or step-by-step approach.")

    if any(keyword in lowered for keyword in expected_keywords(skill_name)):
        strengths.append("You included topic-specific terms that make the response more relevant.")
    else:
        improvements.append("Add the key topic terms or exam vocabulary that would earn marks.")

    mark_deducted = round(max(0.0, 100.0 - score), 1)
    return {
        "summary": f"{summary}\nWhat you did well: {', '.join(strengths[:2]) if strengths else 'You attempted to answer directly and with some structure.'}\nWhere marks were deducted: {', '.join(improvements[:2]) if improvements else 'A few details could be strengthened.'}\nOverall mark deduction: {mark_deducted}%",
        "strengths": strengths,
        "improvements": improvements,
        "score": round(score, 1),
        "mark_deducted": mark_deducted,
    }


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
        score = evaluate_response(skill["name"], response)
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
        conn.execute(
            "INSERT INTO practice_logs (user_id, skill_id, score, readiness_before, readiness_after, practiced_at, response_text, feedback_summary, feedback_rubric) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user["id"], skill_id, score, before, after, now.isoformat(), response, guidance, json.dumps(rubric)),
        )
        conn.commit()
        conn.close()

        flash(f"Readiness updated: {round(after, 1)}%")
        flash(rubric["summary"])
        flash(f"AI Guidance: {guidance}")
        return redirect(url_for("index"))

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
