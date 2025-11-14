from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import os, sqlite3, threading, requests
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "krabz_secret_key_2025"

UPLOAD_FOLDER = 'static/uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'webm'}

# -----------------------
# Translation cache + lock
# -----------------------
translate_cache = {}
lock = threading.Lock()

# -----------------------
# Background storage file
# -----------------------
BACKGROUND_FILE = "background.json"
if not os.path.exists(BACKGROUND_FILE):
    with open(BACKGROUND_FILE, "w") as f:
        f.write('{"type":"default","value":""}')

def get_background():
    import json
    with open(BACKGROUND_FILE, "r") as f:
        return json.load(f)

def set_background(bg_type, value):
    import json
    with open(BACKGROUND_FILE, "w") as f:
        json.dump({"type": bg_type, "value": value}, f)

# -----------------------
# Helpers
# -----------------------
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_db():
    conn = sqlite3.connect('menu.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS menu_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT,
        name_en TEXT,
        name_ar TEXT,
        price TEXT,
        origin TEXT,
        process TEXT,
        flavors TEXT,
        image TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE
    )''')

    existing = c.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    if existing == 0:
        defaults = ['Black','White','Filter','Specials','Pastries','Sweets','Water']
        c.executemany("INSERT INTO categories (name) VALUES (?)", [(d,) for d in defaults])

    conn.commit()
    conn.close()

init_db()

# -----------------------
# Admin credentials
# -----------------------
ADMIN_USER = {
    "username": "admin",
    "email": "krabz@collectiveforlife.com",
    "password": "krabzcoffee"
}

# -----------------------
# REAL ARABIC TRANSLATION
# -----------------------
def translate_arabic(text, target):
    try:
        url = "https://api.mymemory.translated.net/get"
        params = {"q": text, "langpair": f"en|{target}"}
        r = requests.get(url, params=params, timeout=5)
        data = r.json()
        return data.get("responseData", {}).get("translatedText", text)
    except:
        return text

def translate_cached(text, target):
    key = f"{target}:{text.strip().lower()}"
    with lock:
        if key in translate_cache:
            return translate_cache[key]
    translated = translate_arabic(text, target)
    with lock:
        translate_cache[key] = translated
    return translated

# -----------------------
# ROUTES
# -----------------------

@app.route('/')
def landing():
    bg = get_background()
    return render_template('landing.html', bg=bg)

@app.route('/menu')
def menu():
    conn = get_db()
    items = conn.execute("SELECT * FROM menu_items").fetchall()
    conn.close()
    bg = get_background()
    return render_template('menu.html', menu_items=items, bg=bg)

# -----------------------
# BACKGROUND SETTINGS API (FIXES 404)
# -----------------------
@app.route('/background/settings')
def bg_settings():
    bg = get_background()
    bg_type = bg["type"]
    value = bg["value"]

    # Build full static path
    if bg_type == "video" or bg_type == "image":
        path = f"/static/uploads/{value}"
    else:
        path = ""

    return jsonify({
        "type": bg_type,
        "path": path,
        "value": value
    })

# -----------------------
# ADMIN DASHBOARD
# -----------------------
@app.route('/admin', methods=['GET','POST'])
def admin():
    if not session.get('auth'):
        return redirect(url_for('auth'))

    conn = get_db()
    categories = [r['name'] for r in conn.execute("SELECT name FROM categories").fetchall()]

    if request.method == 'POST':
        name_en = request.form['name_en'].strip()
        name_ar = request.form['name_ar'].strip()

        if not name_ar and name_en:
            name_ar = translate_cached(name_en, 'ar')
        elif not name_en and name_ar:
            name_en = translate_cached(name_ar, 'en')

        file = request.files.get('image')
        filename = None
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        conn.execute('''INSERT INTO menu_items
            (category, name_en, name_ar, price, origin, process, flavors, image)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (
                request.form['category'], name_en, name_ar,
                request.form['price'], request.form.get('origin'),
                request.form.get('process'), request.form.get('flavors'),
                filename
            ))
        conn.commit()

    items = conn.execute("SELECT * FROM menu_items").fetchall()
    conn.close()
    return render_template('admin.html', menu_items=items, categories=categories)

# -----------------------
# EDIT ITEM
# -----------------------
@app.route('/edit/<int:item_id>', methods=['GET','POST'])
def edit(item_id):
    if not session.get('auth'):
        return redirect(url_for('auth'))

    conn = get_db()
    item = conn.execute("SELECT * FROM menu_items WHERE id=?", (item_id,)).fetchone()
    categories = [r['name'] for r in conn.execute("SELECT name FROM categories").fetchall()]

    if not item:
        conn.close()
        return redirect(url_for('admin'))

    if request.method == 'POST':
        name_en = request.form['name_en'].strip()
        name_ar = request.form['name_ar'].strip()

        if not name_ar and name_en:
            name_ar = translate_cached(name_en, 'ar')
        elif not name_en and name_ar:
            name_en = translate_cached(name_ar, 'en')

        file = request.files.get('image')
        filename = item['image']
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        conn.execute('''UPDATE menu_items SET
            category=?, name_en=?, name_ar=?, price=?,
            origin=?, process=?, flavors=?, image=? WHERE id=?''',
            (
                request.form['category'], name_en, name_ar,
                request.form['price'], request.form.get('origin'),
                request.form.get('process'), request.form.get('flavors'),
                filename, item_id
            ))
        conn.commit()
        conn.close()
        return redirect(url_for('admin'))

    conn.close()
    return render_template('admin_edit.html', item=item, categories=categories)

@app.route('/delete/<int:item_id>')
def delete(item_id):
    if not session.get('auth'):
        return redirect(url_for('auth'))
    conn = get_db()
    conn.execute("DELETE FROM menu_items WHERE id=?", (item_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin'))

# -----------------------
# CATEGORY MANAGEMENT
# -----------------------
@app.route('/categories/add', methods=['POST'])
def add_category():
    new_cat = request.json.get('name', '').strip()
    if not new_cat:
        return jsonify({"status": "empty"})
    conn = get_db()
    try:
        conn.execute("INSERT INTO categories (name) VALUES (?)", (new_cat,))
        conn.commit()
        status = "added"
    except sqlite3.IntegrityError:
        status = "exists"
    conn.close()
    return jsonify({"status": status})

@app.route('/categories/delete', methods=['POST'])
def delete_category():
    name = request.json.get('name', '').strip()
    if not name:
        return jsonify({"status": "empty"})
    conn = get_db()
    conn.execute("DELETE FROM categories WHERE name=?", (name,))
    conn.commit()
    conn.close()
    return jsonify({"status": "deleted"})

# -----------------------
# TRANSLATION ROUTES
# -----------------------
@app.route('/translate', methods=['POST'])
def translate_text():
    data = request.get_json()
    text = data.get('text', '').strip()
    target = data.get('target', 'ar')
    if not text:
        return jsonify({'translated': ''})
    return jsonify({'translated': translate_cached(text, target)})

@app.route('/translate_all', methods=['POST'])
def translate_all():
    data = request.get_json()
    texts = data.get('texts', [])
    target = data.get('target', 'ar')
    translations = [translate_cached(t, target) for t in texts]
    return jsonify({'translations': translations})

# -----------------------
# BACKGROUND EDITOR ROUTES
# -----------------------
@app.route('/admin/background', methods=['GET', 'POST'])
def admin_background():
    if not session.get('auth'):
        return redirect('/auth')

    bg = get_background()

    if request.method == 'POST':
        bg_type = request.form.get("bg_type")

        if bg_type == "color":
            color = request.form.get("bg_color")
            set_background("color", color)

        elif bg_type == "image":
            file = request.files.get("bg_image")
            if file and allowed_file(file.filename):
                filename = "bg_" + secure_filename(file.filename)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                set_background("image", filename)

        elif bg_type == "video":
            file = request.files.get("bg_video")
            if file and allowed_file(file.filename):
                filename = "bg_" + secure_filename(file.filename)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                set_background("video", filename)

        elif bg_type == "default":
            set_background("default", "")

        return redirect('/admin/background')

    return render_template("admin_background.html", bg=bg)

# -----------------------
# AUTH
# -----------------------
@app.route('/auth', methods=['GET','POST'])
def auth():
    error = None
    if request.method == 'POST':
        user_input = request.form['username']
        password = request.form['password']
        if (user_input.lower() in [ADMIN_USER['username'].lower(), ADMIN_USER['email'].lower()]
            and password == ADMIN_USER['password']):
            session['auth'] = True
            return redirect(url_for('admin'))
        else:
            error = "Access Denied — Wrong Credentials"
    return render_template('auth.html', error=error)

@app.route('/logout')
def logout():
    session.pop('auth', None)
    return redirect(url_for('menu'))

# -----------------------
# RUN APP
# -----------------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
