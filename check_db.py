import sqlite3

conn = sqlite3.connect('skills.db')
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
print("Tables:", [t[0] for t in tables])

if ('skills',) in tables:
    skills_cols = conn.execute("PRAGMA table_info(skills);").fetchall()
    print("Skills columns:", [col[1] for col in skills_cols])
else:
    print("Skills table not found")

conn.close()
