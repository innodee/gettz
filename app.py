from __future__ import annotations

import os
import csv
from datetime import datetime, date
from calendar import monthrange
from io import StringIO, BytesIO
from functools import wraps

from dotenv import load_dotenv

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    Response,
    send_file,
    abort,
    session,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func

# PDF (Monthly close)
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet

# -------------------
# Environment
# -------------------
load_dotenv()  # loads .env into environment variables

ADMIN_USERNAME = os.getenv("BAR_ADMIN_USERNAME")
ADMIN_PASSWORD = os.getenv("BAR_ADMIN_PASSWORD")

if not ADMIN_USERNAME or not ADMIN_PASSWORD:
    raise RuntimeError("Missing BAR_ADMIN_USERNAME or BAR_ADMIN_PASSWORD in .env file")

SECRET_KEY = os.getenv("BAR_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("Missing BAR_SECRET_KEY in .env file")

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# -------------------
# App config
# -------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET_KEY
#app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(BASE_DIR, "barbook.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "barbook.db")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + DB_PATH

db = SQLAlchemy(app)

# -------------------
# Models
# -------------------
class DailySale(db.Model):
    __tablename__ = "daily_sales"

    id = db.Column(db.Integer, primary_key=True)
    sale_date = db.Column(db.Date, nullable=False, index=True, unique=True)
    total_sales = db.Column(db.Float, nullable=False)
    daily_profit = db.Column(db.Float, nullable=False)

    @property
    def day_name(self) -> str:
        return self.sale_date.strftime("%A")


class Expense(db.Model):
    __tablename__ = "expenses"

    id = db.Column(db.Integer, primary_key=True)
    expense_date = db.Column(db.Date, nullable=False, index=True)
    description = db.Column(db.String(255), nullable=False)
    amount = db.Column(db.Float, nullable=False)

# -------------------
# Helpers
# -------------------
def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def month_bounds(yyyy_mm: str):
    y, m = map(int, yyyy_mm.split("-"))
    start = date(y, m, 1)
    end = date(y, m, monthrange(y, m)[1])
    return start, end


def csv_response(filename: str, header: list[str], rows: list[list[str]]) -> Response:
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for r in rows:
        writer.writerow(r)
    data = buf.getvalue()
    return Response(
        data,
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def login_required_simple(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("login", next=request.path))
        return view_func(*args, **kwargs)

    return wrapper

# -------------------
# Auth routes (Session-based)
# -------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("is_admin"):
        return redirect(url_for("summary"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["is_admin"] = True
            flash("Logged in successfully.", "success")
            return redirect(request.args.get("next") or url_for("summary"))

        flash("Invalid username or password.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("login"))

# -------------------
# Main routes (protected)
# -------------------
@app.route("/")
@login_required_simple
def home():
    return redirect(url_for("summary"))

# ---- Sales ----
@app.route("/sales", methods=["GET", "POST"])
@login_required_simple
def sales():
    if request.method == "POST":
        try:
            d = parse_date(request.form["sale_date"])
            total_sales = float(request.form["total_sales"])
            daily_profit = float(request.form["daily_profit"])

            if total_sales < 0 or daily_profit < 0:
                raise ValueError("Values must be non-negative.")

            existing = DailySale.query.filter_by(sale_date=d).first()
            if existing:
                existing.total_sales = total_sales
                existing.daily_profit = daily_profit
                flash(f"Updated sale for {d}.", "info")
            else:
                db.session.add(
                    DailySale(
                        sale_date=d,
                        total_sales=total_sales,
                        daily_profit=daily_profit,
                    )
                )
                flash(f"Added sale for {d}.", "success")

            db.session.commit()
        except Exception as e:
            db.session.rollback()
            flash(f"Could not save sale: {e}", "error")
        return redirect(url_for("sales"))

    rows = DailySale.query.order_by(DailySale.sale_date.desc()).limit(90).all()
    return render_template("sales.html", rows=rows)


@app.route("/sales/<int:sale_id>/edit", methods=["GET", "POST"])
@login_required_simple
def edit_sale(sale_id: int):
    sale = DailySale.query.get_or_404(sale_id)

    if request.method == "POST":
        try:
            d = parse_date(request.form["sale_date"])
            total_sales = float(request.form["total_sales"])
            daily_profit = float(request.form["daily_profit"])

            if total_sales < 0 or daily_profit < 0:
                raise ValueError("Values must be non-negative.")

            other = DailySale.query.filter(
                DailySale.sale_date == d, DailySale.id != sale.id
            ).first()
            if other:
                raise ValueError("Another sale entry already exists for that date.")

            sale.sale_date = d
            sale.total_sales = total_sales
            sale.daily_profit = daily_profit

            db.session.commit()
            flash("Sale updated.", "success")
            return redirect(url_for("sales"))
        except Exception as e:
            db.session.rollback()
            flash(f"Could not update sale: {e}", "error")

    return render_template("sale_edit.html", sale=sale)


@app.route("/sales/<int:sale_id>/delete", methods=["POST"])
@login_required_simple
def delete_sale(sale_id: int):
    sale = DailySale.query.get_or_404(sale_id)
    try:
        db.session.delete(sale)
        db.session.commit()
        flash("Sale deleted.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Could not delete sale: {e}", "error")
    return redirect(url_for("sales"))

# ---- Expenses ----
@app.route("/expenses", methods=["GET", "POST"])
@login_required_simple
def expenses():
    if request.method == "POST":
        try:
            d = parse_date(request.form["expense_date"])
            desc = request.form["description"].strip()
            amt = float(request.form["amount"])

            if not desc:
                raise ValueError("Description is required.")
            if amt < 0:
                raise ValueError("Amount must be non-negative.")

            db.session.add(Expense(expense_date=d, description=desc, amount=amt))
            db.session.commit()
            flash("Added expense.", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Could not save expense: {e}", "error")

        return redirect(url_for("expenses"))

    rows = Expense.query.order_by(Expense.expense_date.desc(), Expense.id.desc()).limit(150).all()
    return render_template("expenses.html", rows=rows)


@app.route("/expenses/<int:expense_id>/edit", methods=["GET", "POST"])
@login_required_simple
def edit_expense(expense_id: int):
    exp = Expense.query.get_or_404(expense_id)

    if request.method == "POST":
        try:
            d = parse_date(request.form["expense_date"])
            desc = request.form["description"].strip()
            amt = float(request.form["amount"])

            if not desc:
                raise ValueError("Description is required.")
            if amt < 0:
                raise ValueError("Amount must be non-negative.")

            exp.expense_date = d
            exp.description = desc
            exp.amount = amt

            db.session.commit()
            flash("Expense updated.", "success")
            return redirect(url_for("expenses"))
        except Exception as e:
            db.session.rollback()
            flash(f"Could not update expense: {e}", "error")

    return render_template("expense_edit.html", exp=exp)


@app.route("/expenses/<int:expense_id>/delete", methods=["POST"])
@login_required_simple
def delete_expense(expense_id: int):
    exp = Expense.query.get_or_404(expense_id)
    try:
        db.session.delete(exp)
        db.session.commit()
        flash("Expense deleted.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Could not delete expense: {e}", "error")
    return redirect(url_for("expenses"))

# ---- Summary ----
@app.route("/summary")
@login_required_simple
def summary():
    default_month = date.today().strftime("%Y-%m")
    month = request.args.get("month", default_month)

    try:
        start, end = month_bounds(month)
    except Exception:
        month = default_month
        start, end = month_bounds(default_month)
        flash("Invalid month selected; showing current month instead.", "info")

    total_sales = (
        db.session.query(func.coalesce(func.sum(DailySale.total_sales), 0.0))
        .filter(DailySale.sale_date.between(start, end))
        .scalar()
    )
    total_profit = (
        db.session.query(func.coalesce(func.sum(DailySale.daily_profit), 0.0))
        .filter(DailySale.sale_date.between(start, end))
        .scalar()
    )
    total_expenses = (
        db.session.query(func.coalesce(func.sum(Expense.amount), 0.0))
        .filter(Expense.expense_date.between(start, end))
        .scalar()
    )

    net_profit = float(total_profit) - float(total_expenses)

    sales_rows = (
        DailySale.query.filter(DailySale.sale_date.between(start, end))
        .order_by(DailySale.sale_date.asc())
        .all()
    )
    expense_rows = (
        Expense.query.filter(Expense.expense_date.between(start, end))
        .order_by(Expense.expense_date.asc(), Expense.id.asc())
        .all()
    )

    return render_template(
        "summary.html",
        month=month,
        start=start,
        end=end,
        total_sales=float(total_sales),
        total_profit=float(total_profit),
        total_expenses=float(total_expenses),
        net_profit=float(net_profit),
        sales_rows=sales_rows,
        expense_rows=expense_rows,
    )

# -------------------
# Exports / Backup (protected)
# -------------------
@app.route("/export/sales.csv")
@login_required_simple
def export_sales_csv():
    rows = DailySale.query.order_by(DailySale.sale_date.asc()).all()
    data = [
        [r.sale_date.isoformat(), r.day_name, f"{r.total_sales:.2f}", f"{r.daily_profit:.2f}"]
        for r in rows
    ]
    return csv_response("daily_sales.csv", ["date", "day", "total_sales", "daily_profit"], data)


@app.route("/export/expenses.csv")
@login_required_simple
def export_expenses_csv():
    rows = Expense.query.order_by(Expense.expense_date.asc(), Expense.id.asc()).all()
    data = [[r.expense_date.isoformat(), r.description, f"{r.amount:.2f}"] for r in rows]
    return csv_response("expenses.csv", ["date", "description", "amount"], data)


@app.route("/export/summary.csv")
@login_required_simple
def export_summary_csv():
    default_month = date.today().strftime("%Y-%m")
    month = request.args.get("month", default_month)

    try:
        start, end = month_bounds(month)
    except Exception:
        month = default_month
        start, end = month_bounds(default_month)

    total_sales = (
        db.session.query(func.coalesce(func.sum(DailySale.total_sales), 0.0))
        .filter(DailySale.sale_date.between(start, end))
        .scalar()
    )
    total_profit = (
        db.session.query(func.coalesce(func.sum(DailySale.daily_profit), 0.0))
        .filter(DailySale.sale_date.between(start, end))
        .scalar()
    )
    total_expenses = (
        db.session.query(func.coalesce(func.sum(Expense.amount), 0.0))
        .filter(Expense.expense_date.between(start, end))
        .scalar()
    )
    net_profit = float(total_profit) - float(total_expenses)

    return csv_response(
        f"summary_{month}.csv",
        ["month", "total_sales", "total_expenses", "total_daily_profit", "net_profit"],
        [[
            month,
            f"{float(total_sales):.2f}",
            f"{float(total_expenses):.2f}",
            f"{float(total_profit):.2f}",
            f"{net_profit:.2f}",
        ]],
    )


@app.route("/backup/db")
@login_required_simple
def backup_db():
    db_path = os.path.join(BASE_DIR, "barbook.db")
    if not os.path.exists(db_path):
        abort(404, "Database file not found yet. Run the app once and add some entries.")
    return send_file(db_path, as_attachment=True, download_name="barbook_backup.db")

# -------------------
# Monthly Close PDF (protected)
# -------------------
@app.route("/report/monthly.pdf")
@login_required_simple
def monthly_close_pdf():
    default_month = date.today().strftime("%Y-%m")
    month = request.args.get("month", default_month)

    try:
        start, end = month_bounds(month)
    except Exception:
        month = default_month
        start, end = month_bounds(default_month)

    total_sales = (
        db.session.query(func.coalesce(func.sum(DailySale.total_sales), 0.0))
        .filter(DailySale.sale_date.between(start, end))
        .scalar()
    )
    total_profit = (
        db.session.query(func.coalesce(func.sum(DailySale.daily_profit), 0.0))
        .filter(DailySale.sale_date.between(start, end))
        .scalar()
    )
    total_expenses = (
        db.session.query(func.coalesce(func.sum(Expense.amount), 0.0))
        .filter(Expense.expense_date.between(start, end))
        .scalar()
    )
    net_profit = float(total_profit) - float(total_expenses)

    sales_rows = (
        DailySale.query.filter(DailySale.sale_date.between(start, end))
        .order_by(DailySale.sale_date.asc())
        .all()
    )
    expense_rows = (
        Expense.query.filter(Expense.expense_date.between(start, end))
        .order_by(Expense.expense_date.asc(), Expense.id.asc())
        .all()
    )

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"Monthly Close - {month}",
    )

    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("<b>Monthly Close Report</b>", styles["Title"]))
    story.append(Paragraph(f"<b>Month:</b> {month}", styles["Normal"]))
    story.append(Paragraph(f"<b>Period:</b> {start.isoformat()} to {end.isoformat()}", styles["Normal"]))
    story.append(Spacer(1, 10))

    story.append(Paragraph("<b>Summary</b>", styles["Heading2"]))
    summary_data = [
        ["Total Sales", f"{float(total_sales):.2f}"],
        ["Total Expenses", f"{float(total_expenses):.2f}"],
        ["Total Daily Profit", f"{float(total_profit):.2f}"],
        ["Net Profit", f"{float(net_profit):.2f}"],
    ]
    summary_table = Table(summary_data, colWidths=[80 * mm, 80 * mm])
    summary_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("ALIGN", (0, 0), (0, -1), "LEFT"),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("BACKGROUND", (0, -1), (-1, -1), colors.lightgrey),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ]
        )
    )
    story.append(summary_table)
    story.append(Spacer(1, 14))

    story.append(Paragraph("<b>Daily Sales</b>", styles["Heading2"]))
    sales_data = [["Date", "Day", "Total Sales", "Daily Profit"]]
    for r in sales_rows:
        sales_data.append([r.sale_date.isoformat(), r.day_name, f"{r.total_sales:.2f}", f"{r.daily_profit:.2f}"])
    if len(sales_data) == 1:
        sales_data.append(["-", "-", "0.00", "0.00"])

    sales_table = Table(sales_data, colWidths=[32 * mm, 34 * mm, 50 * mm, 50 * mm])
    sales_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f7f7")]),
            ]
        )
    )
    story.append(sales_table)
    story.append(Spacer(1, 14))

    story.append(Paragraph("<b>Expenses</b>", styles["Heading2"]))
    exp_data = [["Date", "Description", "Amount"]]
    for e in expense_rows:
        exp_data.append([e.expense_date.isoformat(), e.description, f"{e.amount:.2f}"])
    if len(exp_data) == 1:
        exp_data.append(["-", "-", "0.00"])

    exp_table = Table(exp_data, colWidths=[32 * mm, 104 * mm, 30 * mm])
    exp_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (2, 1), (2, -1), "RIGHT"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f7f7")]),
            ]
        )
    )
    story.append(exp_table)

    doc.build(story)
    buffer.seek(0)

    filename = f"monthly_close_{month}.pdf"
    return Response(
        buffer.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

# -------------------
# Entrypoint
# -------------------

# Ensure DB tables exist in production (e.g., when started via Gunicorn)
with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(debug=True)
