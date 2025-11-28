from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import os, sqlite3, threading, requests, json
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "krabz_secret_key_2025"

# ------------------------------------
# UPLOAD CONFIG
# ------------------------------------
UPLOAD_FOLDER = 'static/uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# FULL video + photo support
ALLOWED_EXTENSIONS = {
    'png','jpg','jpeg','gif',
    'mp4','webm','mov','m4v','ogg','avi','mkv',
    'heic','heif','avif'
}
VIDEO_EXTENSIONS = {'mp4','webm','mov','m4v','ogg','avi','mkv'}

def is_video(ext):
    return ext.lower() in VIDEO_EXTENSIONS

def allowed_file(filename):
    if not filename or "." not in filename:
        return False
    filename = filename.strip().replace(" ", "")
    ext = filename.rsplit(".", 1)[1].lower()
    if ext in {'heic', 'heif', 'avif'}:
        return True
    return ext in ALLOWED_EXTENSIONS


# ------------------------------------
# Translation cache
# ------------------------------------
translate_cache = {}
lock = threading.Lock()

# ------------------------------------
# Background JSON storage
# ------------------------------------
BACKGROUND_FILE = "background.json"
if not os.path.exists(BACKGROUND_FILE):
    with open(BACKGROUND_FILE, "w") as f:
        f.write('{"type":"default","value":""}')

def get_background():
    import json as _json
    with open(BACKGROUND_FILE, "r") as f:
        return _json.load(f)

def set_background(bg_type, value):
    import json as _json
    with open(BACKGROUND_FILE, "w") as f:
        _json.dump({"type": bg_type, "value": value}, f)


# ------------------------------------
# Database
# ------------------------------------
def get_db():
    conn = sqlite3.connect('menu.db')
    conn.row_factory = sqlite3.Row
    return conn

def safe_add_column(name, type_):
    conn = get_db()
    try:
        conn.execute(f"ALTER TABLE menu_items ADD COLUMN {name} {type_}")
        conn.commit()
    except:
        pass
    conn.close()

def init_db():
    conn = get_db()
    c = conn.cursor()

    # Menu item table
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

    # ✓ Auto-upgrade with new fields
    safe_add_column("milk_extra_price", "REAL DEFAULT 0")
    safe_add_column("variants", "TEXT")
    safe_add_column("stock_mode", "TEXT DEFAULT 'status'")
    safe_add_column("stock_status", "TEXT DEFAULT 'Available'")
    safe_add_column("stock_quantity", "INTEGER DEFAULT 0")
    safe_add_column("use_milk", "INTEGER DEFAULT 1")
    safe_add_column("use_variant", "INTEGER DEFAULT 1")
    safe_add_column("use_cup", "INTEGER DEFAULT 1")
    safe_add_column("use_extrashot", "INTEGER DEFAULT 1")

    # Categories
    c.execute('''CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE
    )''')

    # Orders
    c.execute('''CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        items TEXT,
        total REAL,
        notes TEXT,
        status TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Default categories
    if c.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
        defaults = ['Black', 'White', 'Filter', 'Specials', 'Pastries', 'Sweets', 'Water']
        c.executemany("INSERT INTO categories (name) VALUES (?)", [(d,) for d in defaults])

    conn.commit()
    conn.close()

init_db()

# ------------------------------------
# Admin account
# ------------------------------------
ADMIN_USER = {
    "username": "admin",
    "email": "krabz@collectiveforlife.com",
    "password": "krabzcoffee"
}

# ------------------------------------
# Translation
# ------------------------------------
def translate_arabic(text, target):
    try:
        url = "https://api.mymemory.translated.net/get"
        langpair = "en|ar" if target=="ar" else "ar|en"
        r = requests.get(url, params={"q": text, "langpair": langpair}, timeout=5)
        return r.json().get("responseData", {}).get("translatedText", text)
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


# ------------------------------------
# ROUTES
# ------------------------------------
@app.route('/')
def landing():
    return render_template("landing.html", bg=get_background())


@app.route('/menu')
def menu():
    conn = get_db()
    items = conn.execute("SELECT * FROM menu_items").fetchall()
    conn.close()

    grouped = {}
    for it in items:
        cat = it["category"] or "Other"
        grouped.setdefault(cat, []).append(it)

    return render_template(
        "menu.html",
        menu_items=items,
        grouped=grouped,
        bg=get_background()
    )


@app.route('/cart')
def cart():
    return render_template("cart.html")


# ------------------------------------
# Background API
# ------------------------------------
@app.route('/background/settings')
def bg_settings():
    bg = get_background()
    if bg["type"] in ["video", "image"] and bg["value"]:
        ext = bg["value"].split(".")[-1].lower()
        mtype = "video" if is_video(ext) else "image"
        path = f"/static/uploads/{bg['value']}"
    else:
        mtype = "default"
        path = ""
    return jsonify({
        "type": bg["type"],
        "value": bg["value"],
        "path": path,
        "media_type": mtype
    })


# ------------------------------------
# Admin
# ------------------------------------
@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if not session.get("auth"):
        return redirect('/auth')

    conn = get_db()
    categories = [r["name"] for r in conn.execute("SELECT name FROM categories")]

    # 🔥 SAVE NEW ITEM
    if request.method == "POST":

        # Detect checkbox toggles
        use_milk = 1 if request.form.get("use_milk") else 0
        use_variant = 1 if request.form.get("use_variant") else 0
        use_cup = 1 if request.form.get("use_cup") else 0
        use_extrashot = 1 if request.form.get("use_extrashot") else 0

        name_en = request.form["name_en"].strip()
        name_ar = request.form["name_ar"].strip()

        if not name_ar and name_en:
            name_ar = translate_cached(name_en, "ar")
        elif not name_en and name_ar:
            name_en = translate_cached(name_ar, "en")

        # Image
        file = request.files.get("image")
        filename = None
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file.save(os.path.join(UPLOAD_FOLDER, filename))

        conn.execute('''INSERT INTO menu_items
            (category, name_en, name_ar, price,
             origin, process, flavors, image,
             milk_extra_price, variants,
             stock_status, stock_quantity,
             use_milk, use_variant, use_cup, use_extrashot)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (
                request.form["category"],
                name_en,
                name_ar,
                request.form["price"],
                request.form.get("origin"),
                request.form.get("process"),
                request.form.get("flavors"),
                filename,
                request.form.get("milk_extra_price") or 0,
                request.form.get("variants") or "[]",
                request.form.get("stock_status") or "Available",
                request.form.get("stock_quantity") or 0,
                use_milk,
                use_variant,
                use_cup,
                use_extrashot
            )
        )
        conn.commit()

    items = conn.execute("SELECT * FROM menu_items").fetchall()
    conn.close()

    return render_template("admin.html", menu_items=items, categories=categories)


# ------------------------------------
# Edit Item
# ------------------------------------
@app.route('/edit/<int:item_id>', methods=['GET', 'POST'])
def edit(item_id):
    if not session.get("auth"):
        return redirect("/auth")

    conn = get_db()
    item = conn.execute("SELECT * FROM menu_items WHERE id=?", (item_id,)).fetchone()
    categories = [r["name"] for r in conn.execute("SELECT name FROM categories")]

    if not item:
        conn.close()
        return redirect("/admin")

    if request.method == "POST":
        # Detect new toggles
        use_milk = 1 if request.form.get("use_milk") else 0
        use_variant = 1 if request.form.get("use_variant") else 0
        use_cup = 1 if request.form.get("use_cup") else 0
        use_extrashot = 1 if request.form.get("use_extrashot") else 0

        name_en = request.form["name_en"].strip()
        name_ar = request.form["name_ar"].strip()

        if not name_ar and name_en:
            name_ar = translate_cached(name_en, "ar")
        elif not name_en and name_ar:
            name_en = translate_cached(name_ar, "en")

        file = request.files.get("image")
        filename = item["image"]

        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file.save(os.path.join(UPLOAD_FOLDER, filename))

        conn.execute('''UPDATE menu_items SET
            category=?, name_en=?, name_ar=?, price=?,
            origin=?, process=?, flavors=?, image=?,
            milk_extra_price=?, variants=?,
            stock_status=?, stock_quantity=?,
            use_milk=?, use_variant=?, use_cup=?, use_extrashot=?
            WHERE id=?''',
            (
                request.form["category"],
                name_en,
                name_ar,
                request.form["price"],
                request.form.get("origin"),
                request.form.get("process"),
                request.form.get("flavors"),
                filename,
                request.form.get("milk_extra_price") or 0,
                request.form.get("variants") or "[]",
                request.form.get("stock_status") or "Available",
                request.form.get("stock_quantity") or 0,
                use_milk,
                use_variant,
                use_cup,
                use_extrashot,
                item_id
            )
        )
        conn.commit()
        conn.close()
        return redirect("/admin")

    conn.close()
    return render_template("admin_edit.html", item=item, categories=categories)


# ------------------------------------
# Delete Item
# ------------------------------------
@app.route('/delete/<int:item_id>')
def delete(item_id):
    if not session.get("auth"):
        return redirect("/auth")
    conn = get_db()
    conn.execute("DELETE FROM menu_items WHERE id=?", (item_id,))
    conn.commit()
    conn.close()
    return redirect("/admin")


# ------------------------------------
# Category Control
# ------------------------------------
@app.route('/categories/add', methods=['POST'])
def add_category():
    name = request.json.get("name", "").strip()
    if not name:
        return jsonify({"status": "empty"})
    conn = get_db()
    try:
        conn.execute("INSERT INTO categories (name) VALUES (?)", (name,))
        conn.commit()
        result = "added"
    except sqlite3.IntegrityError:
        result = "exists"
    conn.close()
    return jsonify({"status": result})


@app.route('/categories/delete', methods=['POST'])
def delete_category():
    name = request.json.get("name", "").strip()
    if not name:
        return jsonify({"status": "empty"})
    conn = get_db()
    conn.execute("DELETE FROM categories WHERE name=?", (name,))
    conn.commit()
    conn.close()
    return jsonify({"status": "deleted"})


# ------------------------------------
# Translation Endpoints
# ------------------------------------
@app.route('/translate_all', methods=['POST'])
def translate_all():
    data = request.get_json()
    texts = data.get("texts", [])
    target = data.get("target", "ar")
    translations = [translate_cached(t, target) for t in texts]
    return jsonify({"translations": translations})


@app.route('/translate', methods=['POST'])
def translate_single():
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    target = data.get("target") or data.get("lang") or "ar"
    if not text:
        return jsonify({"translated": ""})
    return jsonify({"translated": translate_cached(text, target)})


# ------------------------------------
# Background Editor
# ------------------------------------
@app.route('/admin/background', methods=['GET','POST'])
def admin_background():
    if not session.get("auth"):
        return redirect("/auth")

    bg = get_background()

    if request.method == "POST":
        bg_type = request.form.get("bg_type")

        if bg_type == "color":
            set_background("color", request.form.get("bg_color"))

        elif bg_type == "image":
            file = request.files.get("bg_image")
            if file and allowed_file(file.filename):
                filename = "bg_" + secure_filename(file.filename)
                file.save(os.path.join(UPLOAD_FOLDER, filename))
                set_background("image", filename)

        elif bg_type == "video":
            file = request.files.get("bg_video")
            if file and allowed_file(file.filename):
                filename = "bg_" + secure_filename(file.filename)
                file.save(os.path.join(UPLOAD_FOLDER, filename))
                set_background("video", filename)

        elif bg_type == "default":
            set_background("default", "")

        return redirect("/admin/background")

    return render_template("admin_background.html", bg=bg)


# ------------------------------------
# Staff
# ------------------------------------
@app.route('/staff')
def staff_dashboard():
    if not session.get("auth"):
        return redirect("/auth")
    return render_template("staff.html")


@app.route('/api/orders/create', methods=['POST'])
def api_orders_create():
    data = request.get_json() or {}
    items = data.get("items", [])
    total = data.get("total", 0)
    notes = data.get("notes", "")

    try:
        total_val = float(total)
    except:
        total_val = 0.0

    conn = get_db()
    conn.execute(
        "INSERT INTO orders (items, total, notes, status) VALUES (?,?,?,?)",
        (json.dumps(items), total_val, notes, "Pending")
    )
    conn.commit()

    oid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.close()
    return jsonify({"status": "ok", "order_id": oid})


@app.route('/api/orders/list')
def api_orders_list():
    if not session.get("auth"):
        return jsonify({"orders": []})

    conn = get_db()
    rows = conn.execute(
        "SELECT id, items, total, notes, status, created_at FROM orders ORDER BY created_at DESC"
    ).fetchall()
    conn.close()

    orders = []
    for r in rows:
        try:
            items = json.loads(r["items"] or "[]")
        except:
            items = []
        orders.append({
            "id": r["id"],
            "items": items,
            "total": r["total"],
            "notes": r["notes"],
            "status": r["status"],
            "created_at": r["created_at"]
        })

    return jsonify({"orders": orders})


@app.route('/api/orders/update_status', methods=['POST'])
def api_orders_update_status():
    if not session.get("auth"):
        return jsonify({"status":"unauthorized"}), 403

    data = request.get_json() or {}
    oid = data.get("order_id")
    status = data.get("status")

    if not oid or not status:
        return jsonify({"status":"missing"})

    conn = get_db()
    conn.execute("UPDATE orders SET status=? WHERE id=?", (status, oid))
    conn.commit()
    conn.close()

    return jsonify({"status":"ok"})


# ------------------------------------
# Auth
# ------------------------------------
@app.route('/auth', methods=['GET','POST'])
def auth():
    error = None
    if request.method == "POST":
        u = request.form["username"]
        p = request.form["password"]

        if (u.lower() in [ADMIN_USER["username"].lower(), ADMIN_USER["email"].lower()]
            and p == ADMIN_USER["password"]):
            session["auth"] = True
            return redirect("/admin")
        else:
            error = "Access Denied — Wrong Credentials"

    return render_template("auth.html", error=error)


@app.route('/logout')
def logout():
    session.pop("auth", None)
    return redirect("/menu")


# ------------------------------------
# Run
# ------------------------------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
