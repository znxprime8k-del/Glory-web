import re
import os

# 1. Delete database to clear corrupt admin
if os.path.exists("data.db"):
    os.remove("data.db")
    print("Old DB deleted.")

# 2. Fix server.py logic to ALWAYS check and create admin
with open('server.py', 'r', encoding='utf-8') as f:
    s = f.read()

# Update ensure_admin to be bulletproof
new_admin_func = '''def ensure_admin():
    with db() as c:
        admin = c.execute("SELECT * FROM users WHERE role='admin'").fetchone()
        if not admin:
            ph = bcrypt.hashpw(ADMIN_PASSWORD[:72].encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            c.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
                      (ADMIN_USERNAME, ph, "admin", now_iso()))
'''

s = re.sub(r'def ensure_admin\(\):.*?(?=\ninit_db\(\))', new_admin_func, s, flags=re.DOTALL)

with open('server.py', 'w', encoding='utf-8') as f:
    f.write(s)

print("Server code updated to recreate admin!")

os.system('git add .')
os.system('git commit -m "Reset DB and fix admin creation"')
os.system('git push origin main -f')
