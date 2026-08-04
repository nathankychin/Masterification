import math
import os
import random
import sqlite3
from datetime import datetime, timedelta

from flask import Flask, flash, redirect, render_template, request, session, url_for, jsonify
from werkzeug.security import check_password_hash, generate_password_hash

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-please-change")

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


def create_user(conn, username, password, role="user"):
    existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if existing:
        return None
    conn.execute(
        "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
        (username, generate_password_hash(password), role, datetime.utcnow().isoformat()),
    )
    return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


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
            created_at TEXT NOT NULL
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
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(skill_id) REFERENCES skills(id)
        )
        """
    )

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


def generate_scenario_ai(skill_name: str, skill_context: str, category: str) -> str:
    """Generate a unique, AI-powered scenario."""
    if not client:
        return generate_scenario_fallback(skill_name, skill_context)
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": f"Create a realistic, high-pressure practice scenario for '{skill_name}' ({category}). Be specific to this exact skill. Respond with ONE scenario prompt (2-3 sentences).",
                },
                {
                    "role": "user",
                    "content": f"Skill: {skill_name}. Why it matters: {skill_context}",
                },
            ],
            temperature=0.8,
            max_tokens=150,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"AI scenario failed: {e}")
        return generate_scenario_fallback(skill_name, skill_context)


def generate_scenario_fallback(skill_name: str, skill_context: str) -> str:
    """Fallback scenario."""
    scenarios = [
        f"You need to apply {skill_name} in a high-pressure situation right now. Walk through what you would do step-by-step.",
        f"A critical moment just emerged where {skill_name} is essential. Show me your first response.",
        f"You're being tested on {skill_name} under time pressure. How would you proceed?",
    ]
    return random.choice(scenarios)


def generate_guidance_ai(skill_name: str, user_response: str, score: int) -> str:
    """Generate personalized AI guidance."""
    if not client:
        return feedback_for(skill_name, score)
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": f"You are a tutor for {skill_name}. Analyze the response and provide: (1) What was done well, (2) What needs improvement, (3) One tip to practice. Be honest but encouraging. Keep it 3-4 sentences.",
                },
                {
                    "role": "user",
                    "content": f"My response: {user_response}\nMy score: {score}/100",
                },
            ],
            temperature=0.6,
            max_tokens=200,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"AI guidance failed: {e}")
        return feedback_for(skill_name, score)


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
    if "first" in lowered and "steps" in lowered:
        score += 1
    if "because" in lowered or "why" in lowered:
        score += 1
    return clamp(score * 20.0, 0.0, 100.0)


def feedback_for(skill_name, score):
    if score >= 80:
        return "Strong readiness. The core actions were clear and timely."
    if score >= 50:
        return "Moderate readiness. The response captured the main idea, but some key steps were missing."
    return "Low readiness. The response was too vague and would likely slow down recall under pressure."


def login_required(route):
    def wrapper(*args, **kwargs):
        if not current_user():
            flash("Please sign in to continue.")
            return redirect(url_for("login"))
        return route(*args, **kwargs)
    wrapper.__name__ = route.__name__
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
        if not username or not password:
            flash("Please provide both a username and password.")
            return redirect(url_for("register"))

        conn = get_db()
        existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if existing:
            conn.close()
            flash("That username is already taken.")
            return redirect(url_for("register"))

        user = create_user(conn, username, password, "user")
        conn.commit()
        conn.close()
        session["user_id"] = user["id"]
        flash("Account created successfully.")
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
            flash("Welcome back.")
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
        flash("Please provide both a skill name and context.")
        return redirect(url_for("index"))

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
        flash("Skill not found.")
        return redirect(url_for("index"))

    if request.method == "POST":
        response = request.form.get("response", "")
        score = evaluate_response(skill["name"], response)
        before = projected_readiness(dict(skill), skill["days_since_practice"])
        after = (before * 0.6) + (score * 0.4)

        now = datetime.utcnow()
        reminder_days = max(2, int(round((20 - score / 5) / max(1.0, calculate_risk_weight(dict(skill))))))
        next_review = now + timedelta(days=reminder_days)

        conn.execute(
            """
            UPDATE skills
            SET readiness = ?, days_since_practice = 0, reminder_days = ?, last_practiced_at = ?, next_review_at = ?
            WHERE id = ?
            """,
            (clamp(after, 0.0, 100.0), reminder_days, now.isoformat(), next_review.isoformat(), skill_id),
        )
        conn.execute(
            "INSERT INTO practice_logs (user_id, skill_id, score, readiness_before, readiness_after, practiced_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user["id"], skill_id, score, before, after, now.isoformat()),
        )
        conn.commit()
        conn.close()

        guidance = generate_guidance_ai(skill["name"], response, score)
        flash(f"Readiness updated: {round(after, 1)}%")
        flash(f"AI Guidance: {guidance}")
        return redirect(url_for("index"))

    scenario = generate_scenario_ai(skill["name"], skill["context"], skill["category"] or "Other")
    practice_mode = get_practice_mode_data(skill["name"], skill["category"] or "Other")
    conn.close()
    return render_template(
        "practice.html",
        skill=dict(skill),
        scenario=scenario,
        practice_mode=practice_mode,
    )


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


if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "0").lower() in {"1", "true", "yes", "on"}
    app.run(host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", 5000)), debug=debug_mode, use_reloader=False)
