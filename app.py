

from werkzeug.security import check_password_hash, generate_password_hash
from flask import Flask, render_template, request, redirect, session
import whisper
from transformers import pipeline
import os
from pymongo import MongoClient
from functools import wraps
from bson.objectid import ObjectId
from werkzeug.utils import secure_filename
ALLOWED_EXTENSIONS = {'mp3', 'wav', 'm4a', 'webm'}
from flask import send_file
import io
from reportlab.platypus import SimpleDocTemplate, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
import subprocess
import datetime


app = Flask(__name__)
app.secret_key = "secret123"

app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 MB

# Upload folder
UPLOAD_FOLDER = "uploads"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER


# Auto create uploads folder
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# MongoDB Atlas Connection
client = MongoClient("mongodb+srv://omprakashlodhi236_db_user:om243502@cluster0.u1frtpz.mongodb.net/?retryWrites=true&w=majority")
db = client["speech_app"]
users_collection = db["users"]

def trim_audio(input_path, output_path):
    subprocess.run([
        "ffmpeg",
        "-i", input_path,
        "-t", "10",   # max 10 sec
        "-c", "copy",
        output_path
    ])

# AI Models
model = whisper.load_model("tiny") 
sentiment = pipeline("sentiment-analysis")

#  Middleware: login required
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user' not in session:
            return redirect("/login")
        return f(*args, **kwargs)
    return wrapper

# Middleware: admin required
def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'role' not in session or session['role'] != 'admin':
            return "Access Denied! Admins only "
        return f(*args, **kwargs)
    return wrapper

# Home route
@app.route("/")
def home():
    return redirect("/login")

# Upload Page (protected)
@app.route("/upload-page")
@login_required
def upload_page():
    return render_template("index.html")

# Upload + Speech to Text
@app.route("/upload", methods=["POST"])
@login_required
def upload():
    if 'audio' not in request.files:
        return "No file selected!"

    file = request.files['audio']

    if file.filename == '':
        return "No file selected!"

    if not allowed_file(file.filename):
        return "Invalid file type!"

    if not allowed_mime(file):
        return "Invalid file content!"

    # language FIX
    language = request.form.get("language") or "auto"

    # secure filename
    filename = secure_filename(file.filename)

    upload_folder = os.path.abspath(app.config["UPLOAD_FOLDER"])
    filepath = os.path.abspath(os.path.join(upload_folder, filename))

    # path traversal protection
    if not filepath.startswith(upload_folder):
        return "Path traversal detected!"

    # save file (ONLY ONCE)
    file.save(filepath)

    print("File received:", file.filename)

    trimmed_path = os.path.join(upload_folder, "trimmed_" + filename)

    try:
        trim_audio(filepath, trimmed_path)
        filepath = trimmed_path
    except Exception as e:
        print("Trim failed:", e)

    # default values
    text = "Transcription failed"
    sentiment_result = [{"label": "N/A", "score": 0}]

    try:
        if language == "auto":
            result = model.transcribe(filepath, fp16=False)
            language = result.get("language", "unknown")  # 🔥 auto detect
        else:
            result = model.transcribe(filepath, language=language, fp16=False)

        text = result["text"].strip()

        if not text:
            text = "No speech detected"

        sentiment_result = sentiment(text)

    except Exception as e:
        print("Error:", e)

    # store history
    history_collection = db["history"]

    history_collection.insert_one({
        "user_email": session['user'],
        "filename": filename,
        "file_path": filepath,
        "transcription": text,
        "sentiment": {
            "label": sentiment_result[0]['label'],
            "score": float(sentiment_result[0]['score'])
        },
        "language": language,
        "duration": 10,
        "created_at": datetime.datetime.utcnow()
    })

    return render_template(
        "index.html",
        transcription=text,
        sentiment=sentiment_result,
        language=language
    )
# Registration Route
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = generate_password_hash(request.form['password'])

        # Check existing user
        if users_collection.find_one({"email": email}):
            return "User already exists!"

        # Insert user
        users_collection.insert_one({
        "name": name,
        "email": email,
        "password": password,
        "role": "user",
        "created_at": datetime.datetime.utcnow(),
        "last_login": None,
         "is_active": True
        })

        return redirect("/login")

    return render_template('register.html')

# Login Route
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        user = users_collection.find_one({"email": email})

        #  check user + password
        if user and check_password_hash(user['password'], password):

            # last login
            users_collection.update_one(
                {"email": email},
                {"$set": {"last_login": datetime.datetime.utcnow()}}
            )

            session['user'] = email
            session['role'] = user.get('role', 'user')
            return redirect("/upload-page")
        else:
            return "Invalid Email or Password!"
    return render_template('login.html')

# Admin Route
@app.route('/admin')
@admin_required
def admin_dashboard():
    users = list(users_collection.find({}, {"password": 0}))  # password hide
    return render_template("admin.html", users=users)

# Delet User Route
@app.route('/delete-user/<user_id>')
@admin_required
def delete_user(user_id):
    users_collection.delete_one({"_id": ObjectId(user_id)})
    return redirect("/admin")

# Logout
@app.route('/logout')
def logout():
    session.clear()
    return redirect("/login")

# txt download route
@app.route('/download-txt')
@login_required
def download_txt():
    text = request.args.get("text")

    buffer = io.StringIO()
    buffer.write(text)
    buffer.seek(0)

    return send_file(
        io.BytesIO(buffer.getvalue().encode()),
        as_attachment=True,
        download_name="transcription.txt",
        mimetype="text/plain"
    )


# pdf download route
@app.route('/download-pdf')
@login_required
def download_pdf():
    text = request.args.get("text")

    buffer = io.BytesIO()

    doc = SimpleDocTemplate(buffer)
    styles = getSampleStyleSheet()

    content = []
    content.append(Paragraph(text, styles["Normal"]))

    doc.build(content)

    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name="transcription.pdf",
        mimetype="application/pdf"
    )
# pagination route
@app.route('/history')
@login_required
def history():
    history_collection = db["history"]

    page = int(request.args.get("page", 1))
    per_page = 5   # per page items

    skip = (page - 1) * per_page

    total = history_collection.count_documents({"email": session['user']})

    data = list(history_collection.find(
        {"email": session['user']},
        {"_id": 0}
    ).sort("timestamp", -1).skip(skip).limit(per_page))

    total_pages = (total + per_page - 1) // per_page

    return render_template(
        "history.html",
        history=data,
        page=page,
        total_pages=total_pages
    )

# validation function
def allowed_file(filename):
    return (
        '.' in filename and
        filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
    )
def allowed_mime(file):
    return file.mimetype.startswith('audio/')
# Run app
if __name__ == "__main__":
    app.run(debug=True)