import os
import re

with open('server.py', 'r', encoding='utf-8') as f:
    s = f.read()

# Replace ensure_admin function with a forced update version
new_admin_func = '''def ensure_admin():
    with db() as c:
        admin = c.execute("SELECT * FROM users WHERE username=?", (ADMIN_USERNAME,)).fetchone()
        ph = bcrypt.hashpw(ADMIN_PASSWORD[:72].encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        
        if not admin:
            # Create fresh admin
            c.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
                      (ADMIN_USERNAME, ph, "admin", now_iso()))
        else:
            # Force update existing admin to correct password and role
            c.execute("UPDATE users SET password_hash=?, role='admin' WHERE username=?", (ph, ADMIN_USERNAME))
'''

# Find and replace the function
s = re.sub(r'def ensure_admin\(\):.*?(?=\ninit_db\(\))', new_admin_func, s, flags=re.DOTALL)

with open('server.py', 'w', encoding='utf-8') as f:
    f.write(s)

print('Admin password forced to reset on startup!')

os.system('git add .')
os.system('git commit -m "Force admin credentials reset"')
os.system('git push origin main')
