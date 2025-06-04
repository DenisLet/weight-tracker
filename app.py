"""
Weight Tracker (Flask + SQLite, BMI + PNL)

Запуск:
    python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
    pip install flask flask_sqlalchemy flask_login
    python app.py
"""
import os
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

from flask import (
    Flask, flash, redirect, render_template, request, url_for
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, current_user,
    login_required, login_user, logout_user
)
from werkzeug.security import check_password_hash, generate_password_hash

# ── конфигурация ───────────────────────────────
BASEDIR = Path(__file__).resolve().parent
DB_URI  = os.getenv("DATABASE_URL", f"sqlite:///{BASEDIR/'weight.db'}")

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", "dev-secret-change-me"),
    SQLALCHEMY_DATABASE_URI=DB_URI,          # ← только один раз
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

@app.context_processor
def inject_date():
    return dict(date=date)

# ─────────────────────────────
# Модели
# ─────────────────────────────
class User(UserMixin, db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)

    height_cm     = db.Column(db.Float)        # рост
    start_weight  = db.Column(db.Float)
    target_weight = db.Column(db.Float)
    goal_start    = db.Column(db.Date)         # точка отсчёта

    weights = db.relationship(
        "Weight", backref="user",
        lazy=True, cascade="all, delete-orphan"
    )

    # ——— password helpers ———
    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)


class Weight(db.Model):
    id      = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer,
                        db.ForeignKey("user.id", ondelete="CASCADE"),
                        nullable=False)
    day     = db.Column(db.Date, nullable=False, default=date.today)
    kg      = db.Column(db.Float, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("user_id", "day", name="uix_user_day"),
    )

# ─────────────────────────────
# Auth helpers
# ─────────────────────────────
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ─────────────────────────────
# Роуты
# ─────────────────────────────
@app.route("/")
@login_required
def index():
    # реальные записи
    real = (
        Weight.query
        .filter_by(user_id=current_user.id)
        .order_by(Weight.day.asc())
        .all()
    )
    items = [(w.day, w.kg, True, w.id) for w in real]

    # виртуальная стартовая точка (если указана)
    if current_user.goal_start and current_user.start_weight is not None:
        if all(d != current_user.goal_start for d, *_ in items):
            items.append((current_user.goal_start,
                          current_user.start_weight, False, None))

    # сортировка
    items.sort(key=lambda t: t[0])

    # подготовка данных для графика
    labels = [d.strftime("%Y-%m-%d") for d, *_ in items]
    data   = [kg for _, kg, *_ in items]

    # расчёт прогресса
    current = data[-1] if data else None
    start, target = current_user.start_weight, current_user.target_weight
    progress = None
    if start and target and current is not None:
        full = abs(start - target) or 1
        progress = min(max(abs(start - current) / full * 100, 0), 100)

    # прошедшие дни от goal_start
    days_elapsed = None
    if current_user.goal_start:
        days_elapsed = (date.today() - current_user.goal_start).days

    # расчёт h² для BMI (если задан рост)
    h2 = None
    if current_user.height_cm:
        h2 = (current_user.height_cm / 100) ** 2   # рост² в метрах

    # строки таблицы
    rows, prev_kg = [], None
    for day_, kg, real_flag, row_id in items:
        pnl = None if prev_kg is None else kg - prev_kg
        bmi = (kg / h2) if h2 else None            # BMI для строки
        rows.append(SimpleNamespace(
            day=day_, kg=kg, pnl=pnl, bmi=bmi,
            real=real_flag, id=row_id))
        prev_kg = kg

    # нормальный диапазон веса (BMI 18.5–25)
    normal_min = normal_max = None
    if h2:
        normal_min = 18.5 * h2
        normal_max = 25.0 * h2

    return render_template(
        "dashboard.html",
        rows=rows,
        labels=labels,
        data=data,
        progress=progress,
        days_elapsed=days_elapsed,
        normal_min=normal_min,
        normal_max=normal_max,
    )

# ---------- регистрация / логин ----------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()
        if not username or not password:
            flash("Укажите логин и пароль", "danger")
            return redirect(url_for("register"))
        if User.query.filter_by(username=username).first():
            flash("Логин уже занят", "warning")
            return redirect(url_for("register"))
        user = User(username=username)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash("Регистрация прошла успешно. Войдите.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            flash("Вход выполнен", "success")
            return redirect(url_for("index"))
        flash("Неверные данные", "danger")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Вы вышли", "info")
    return redirect(url_for("login"))

# ---------- цели и рост ----------
@app.route("/settings", methods=["POST"])
@login_required
def settings():
    current_user.height_cm     = request.form.get("height_cm",   type=float)
    current_user.start_weight  = request.form.get("start_weight",  type=float)
    current_user.target_weight = request.form.get("target_weight", type=float)

    raw_date = request.form.get("goal_start")
    if raw_date:
        try:
            current_user.goal_start = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            flash("Неверный формат даты", "warning")
    else:
        current_user.goal_start = None

    db.session.commit()
    flash("Цели обновлены", "success")
    return redirect(url_for("index"))

# ---------- добавление / редактирование веса ----------
@app.route("/add", methods=["POST"])
@login_required
def add_weight():
    try:
        day = datetime.strptime(request.form["day"], "%Y-%m-%d").date()
        kg  = float(request.form["kg"])
    except Exception:
        flash("Неверный ввод", "danger")
        return redirect(url_for("index"))

    entry = Weight.query.filter_by(user_id=current_user.id, day=day).first()
    if entry:
        entry.kg = kg
    else:
        db.session.add(Weight(user_id=current_user.id, day=day, kg=kg))
    db.session.commit()
    flash("Сохранено", "success")
    return redirect(url_for("index"))

@app.route("/edit/<int:weight_id>", methods=["GET", "POST"])
@login_required
def edit_weight(weight_id):
    entry = Weight.query.get_or_404(weight_id)
    if entry.user_id != current_user.id:
        flash("Доступ запрещён", "danger")
        return redirect(url_for("index"))
    if request.method == "POST":
        entry.day = datetime.strptime(request.form["day"], "%Y-%m-%d").date()
        entry.kg  = float(request.form["kg"])
        db.session.commit()
        flash("Обновлено", "success")
        return redirect(url_for("index"))
    return render_template("edit_weight.html", entry=entry)

@app.route("/delete/<int:weight_id>")
@login_required
def delete_weight(weight_id):
    entry = Weight.query.get_or_404(weight_id)
    if entry.user_id == current_user.id:
        db.session.delete(entry)
        db.session.commit()
        flash("Удалено", "info")
    return redirect(url_for("index"))

# ---------- CLI ----------
@app.cli.command("init-db")
def init_db():
    """Пересоздать таблицы (удаляются все данные!)."""
    db.drop_all()
    db.create_all()
    print("База пересоздана")

# ---------- точка входа ----------
if __name__ == "__main__":
    with app.app_context():
        db.create_all()          # создаём таблицы, если их нет
    app.run(debug=True)
