import os
import html
import functools
import base64
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from supabase import create_client


# =========================================================
# SIZZLELOG - BBQ BUSINESS MANAGEMENT SYSTEM
# Existing Supabase tables are used. No tables are created.
# =========================================================

load_dotenv(override=True)

st.set_page_config(
    page_title="SizzleLog | BBQ Business Management",
    page_icon=next(
        (p for p in ("assets/logo.png", "assets/logo.ico") if Path(p).exists()),
        "🔥",
    ),
    layout="wide",
    initial_sidebar_state="expanded",
)


@functools.lru_cache(maxsize=None)
def _img_css_url(path_str, mime="image/jpeg"):
    p = Path(path_str)
    if not p.exists():
        return "none"
    encoded = base64.b64encode(p.read_bytes()).decode("utf-8")
    return f'url("data:{mime};base64,{encoded}")'


@st.cache_resource(show_spinner=False)
def _build_css(file_path, mtime):
    """Read + assemble the stylesheet once (re-built only when style.css changes)."""
    with open(file_path, "r", encoding="utf-8") as file:
        css = file.read()
    overrides = f":root{{--hero-img:{_img_css_url('assets/hero_bbq.jpg')};}}"
    return f"<style>{css}\n{overrides}</style>"


def load_css(file_path):
    st.markdown(_build_css(file_path, os.path.getmtime(file_path)), unsafe_allow_html=True)


PANEL_CSS = """
<style>
/* fixed width (not "auto"): auto lets tables/columns resize the panel in a loop and it grows forever */
.block-container {
    box-sizing: border-box !important;
    width: calc(100% - 36px) !important;
    max-width: none !important;
    min-width: 0 !important;
    margin: 14px 18px 24px !important;
    padding: 2.2rem 2.4rem 3.2rem !important;
    background: linear-gradient(155deg, rgba(128, 52, 58, 0.55), rgba(66, 24, 28, 0.55));
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 36px;
    box-shadow: 0 30px 80px rgba(0, 0, 0, 0.35);
}
@media (max-width: 640px) {
    .block-container {
        width: calc(100% - 16px) !important;
        margin: 8px !important;
        padding: 1.4rem 1rem 2.4rem !important;
        border-radius: 26px;
    }
}
</style>
"""


load_css("assets/style.css")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# ------------------------- CONNECTION -------------------------

if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Supabase credentials are missing. Check SUPABASE_URL and SUPABASE_KEY in your .env file.")
    st.stop()

try:
    base_supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as exc:
    st.error(f"Could not initialize Supabase: {exc}")
    st.stop()


# ------------------------- HELPERS -------------------------

def money(value):
    try:
        return f"₱{float(value or 0):,.2f}"
    except (ValueError, TypeError):
        return "₱0.00"


def as_float(value):
    try:
        return float(value or 0)
    except (ValueError, TypeError):
        return 0.0


def get_db():
    return st.session_state.get("supabase", base_supabase)


def query_rows(table, columns="*", **filters):
    """Read rows and surface errors instead of silently pretending the table is empty."""
    q = get_db().table(table).select(columns)
    for key, value in filters.items():
        q = q.eq(key, value)
    return q.execute().data or []


def get_categories():
    rows = query_rows("item_categories", "category_id, category_name")
    return [
        {"category_id": int(r["category_id"]), "category_name": (r["category_name"] or "").strip()}
        for r in rows
    ]


def get_products():
    return query_rows(
        "products",
        "product_id, category_id, item_description, selling_price, "
        "busy_day_leftovers_qty, non_busy_day_leftovers_qty, sales_status",
    )


def get_product_map():
    return {
        (int(p["category_id"]), int(p["product_id"])): p
        for p in get_products()
    }


def get_today_preparations():
    today = date.today().isoformat()
    return query_rows(
        "preparations",
        "preparation_id, category_id, product_id, prepared_date, quantity_prepared, cost_per_piece, notes",
        prepared_date=today,
    )


def get_sales_for_date(target_date):
    rows = query_rows("sales", "sale_id, sale_datetime, payment_method, notes")
    prefix = target_date.isoformat()
    return [r for r in rows if str(r.get("sale_datetime", "")).startswith(prefix)]


def get_sold_quantities(preparation_ids=None):
    rows = query_rows("sale_items", "preparation_id, quantity_sold")
    totals = {}
    allowed = set(preparation_ids) if preparation_ids is not None else None
    for row in rows:
        pid = int(row["preparation_id"])
        if allowed is None or pid in allowed:
            totals[pid] = totals.get(pid, 0) + int(row["quantity_sold"])
    return totals


def get_leftover_map(preparation_ids=None):
    rows = query_rows("leftovers", "leftover_id, preparation_id, quantity_remaining, recorded_at, notes")
    allowed = set(preparation_ids) if preparation_ids is not None else None
    return {
        int(r["preparation_id"]): r
        for r in rows
        if allowed is None or int(r["preparation_id"]) in allowed
    }


# ------------------------- SHARED BUSINESS DATA -------------------------
# Every page reads from these helpers (and straight from the database), so a change made
# on one page (prepared stock, a sale, an expense) shows up everywhere on the next refresh.


def parse_timestamp(value):
    """ISO timestamp text -> local datetime (or None)."""
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text[:19])
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def get_user_names():
    """{user_id: display name} for showing who recorded a sale."""
    try:
        rows = query_rows("app_user", "user_id, full_name, username")
    except Exception:
        return {}
    return {
        str(r["user_id"]): (r.get("full_name") or r.get("username") or "Unknown")
        for r in rows
    }


def get_sale_lines(target_date=None):
    """Item-level sales: one dict per product line of every sale (newest first).

    target_date=None returns every date.
    """
    sales = query_rows("sales", "sale_id, sale_datetime, payment_method, recorded_by")
    if target_date is not None:
        prefix = target_date.isoformat()
        sales = [s for s in sales if str(s.get("sale_datetime", "")).startswith(prefix)]
    if not sales:
        return []

    sale_lookup = {int(s["sale_id"]): s for s in sales}
    items = query_rows("sale_items", "sale_id, preparation_id, quantity_sold, unit_price")
    preps = query_rows("preparations", "preparation_id, category_id, product_id")
    prep_lookup = {
        int(p["preparation_id"]): (int(p["category_id"]), int(p["product_id"]))
        for p in preps
    }
    product_map = get_product_map()
    category_names = {int(c["category_id"]): c["category_name"] for c in get_categories()}
    cashiers = get_user_names()

    lines = []
    for item in items:
        sale = sale_lookup.get(int(item["sale_id"]))
        if sale is None:
            continue
        key = prep_lookup.get(int(item["preparation_id"]))
        product = product_map.get(key, {}) if key else {}
        quantity = int(item["quantity_sold"])
        price = as_float(item["unit_price"])
        lines.append({
            "sale_id": int(sale["sale_id"]),
            "sale_datetime": str(sale.get("sale_datetime") or ""),
            "time": parse_timestamp(sale.get("sale_datetime")),
            "payment": sale.get("payment_method") or "Cash",
            "cashier": cashiers.get(str(sale.get("recorded_by")), "Unknown"),
            "category": category_names.get(key[0], "Unknown") if key else "Unknown",
            "product": product.get("item_description", "Product no longer available"),
            "quantity": quantity,
            "unit_price": price,
            "subtotal": quantity * price,
        })
    lines.sort(key=lambda l: (l["sale_datetime"], l["sale_id"]), reverse=True)
    return lines


PERIOD_OPTIONS = ["Today", "This Week", "This Month", "All Time"]


def period_range(label, today=None):
    """(start_date, end_date) for a period label; (None, None) means all time."""
    today = today or date.today()
    if label == "Today":
        return today, today
    if label == "This Week":
        return today - timedelta(days=today.weekday()), today
    if label == "This Month":
        return today.replace(day=1), today
    return None, None


def in_range(day_text, start, end):
    if start is None:
        return True
    try:
        day = date.fromisoformat(str(day_text)[:10])
    except ValueError:
        return False
    return start <= day <= end


def get_financials(start=None, end=None):
    """Sales, expenses and net earnings for a date range (None = all time).

    Net Earnings = Total Sales Amount - Total Expenses.
    """
    sales = [
        s for s in query_rows("sales", "sale_id, sale_datetime")
        if in_range(s.get("sale_datetime"), start, end)
    ]
    sale_ids = {int(s["sale_id"]) for s in sales}
    items = [
        i for i in query_rows("sale_items", "sale_id, quantity_sold, unit_price")
        if int(i["sale_id"]) in sale_ids
    ]
    expenses = [
        e for e in query_rows("expenses", "expense_id, expense_date, amount")
        if in_range(e.get("expense_date"), start, end)
    ]

    sales_total = sum(int(i["quantity_sold"]) * as_float(i["unit_price"]) for i in items)
    expenses_total = sum(as_float(e["amount"]) for e in expenses)
    return {
        "sales_total": sales_total,
        "expenses_total": expenses_total,
        "net": sales_total - expenses_total,
        "transactions": len(sales),
        "units_sold": sum(int(i["quantity_sold"]) for i in items),
        "expense_count": len(expenses),
    }


def live_fragment(run_every):
    """Auto-refreshing block (Streamlit 1.37+). Falls back to a normal block on older versions."""
    def wrap(func):
        fragment = getattr(st, "fragment", None)
        if fragment is None:
            return func
        try:
            return fragment(run_every=run_every)(func)
        except TypeError:
            return func
    return wrap


@live_fragment(5)
def business_summary():
    """Total Sales, Total Expenses and Net Earnings. Re-reads the database every 5 seconds."""
    period = st.radio(
        "Period",
        PERIOD_OPTIONS,
        horizontal=True,
        key="overview_period",
        label_visibility="collapsed",
    )
    start, end = period_range(period)

    try:
        totals = get_financials(start, end)
    except Exception as exc:
        st.error(f"Could not load the business summary: {exc}")
        return

    scope = "Today" if period == "Today" else period
    net = totals["net"]

    m1, m2, m3 = st.columns(3)
    m1.metric(
        "Total Sales Amount",
        money(totals["sales_total"]),
        help=f"Total revenue from all sales recorded ({scope.lower()}).",
    )
    m2.metric(
        "Total Expenses",
        money(totals["expenses_total"]),
        help=f"Everything logged on the Expenses page ({scope.lower()}).",
    )
    m3.metric(
        "Net Earnings (Profit)",
        money(net),
        delta=(f"{money(net)} profit" if net >= 0 else f"-{money(abs(net))} loss"),
        help="Net Earnings = Total Sales Amount - Total Expenses.",
    )

    n1, n2, n3 = st.columns(3)
    n1.metric("Transactions", f"{totals['transactions']:,}")
    n2.metric("Pieces Sold", f"{totals['units_sold']:,}")
    n3.metric("Expense Entries", f"{totals['expense_count']:,}")

    st.caption(
        f"Showing: {scope}  ·  Net Earnings = Total Sales Amount − Total Expenses  ·  "
        f"Live: refreshed {datetime.now().strftime('%I:%M:%S %p')}, then every 5 seconds."
    )


def render_sales_breakdown():
    """Owner view: which products sold, how many units, at what price, and when."""
    import pandas as pd

    f1, f2 = st.columns([2, 1])
    with f1:
        chosen_date = st.date_input("Business Date", value=date.today(), key="sales_breakdown_date")
    with f2:
        st.write("")
        show_all = st.checkbox("Show all dates", key="sales_breakdown_all")

    try:
        lines = get_sale_lines(None if show_all else chosen_date)
    except Exception as exc:
        st.error(f"Could not load sales details: {exc}")
        return

    scope_text = "all dates" if show_all else chosen_date.strftime("%B %d, %Y")

    if not lines:
        st.info(f"No sales have been recorded for {scope_text}.")
        return

    total_sales = sum(l["subtotal"] for l in lines)
    total_units = sum(l["quantity"] for l in lines)
    transactions = len({l["sale_id"] for l in lines})

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Sales", money(total_sales))
    m2.metric("Transactions", f"{transactions:,}")
    m3.metric("Units Sold", f"{total_units:,}")
    m4.metric("Average Sale", money(total_sales / transactions))

    # ---- units sold per product ----
    st.subheader("Units Sold per Product")
    st.caption(f"Sales for {scope_text}. Sorted by units sold.")

    per_product = {}
    for l in lines:
        entry = per_product.setdefault(
            (l["category"], l["product"]),
            {"units": 0, "revenue": 0.0, "sales": set()},
        )
        entry["units"] += l["quantity"]
        entry["revenue"] += l["subtotal"]
        entry["sales"].add(l["sale_id"])

    product_rows = sorted(
        (
            {
                "Category": category,
                "Product": product,
                "Units Sold": data["units"],
                "Avg Unit Price": data["revenue"] / data["units"] if data["units"] else 0.0,
                "Sales Amount": data["revenue"],
                "In Transactions": len(data["sales"]),
            }
            for (category, product), data in per_product.items()
        ),
        key=lambda r: (-r["Units Sold"], r["Product"]),
    )

    st.dataframe(
        product_rows,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Units Sold": st.column_config.NumberColumn(format="%d"),
            "Avg Unit Price": st.column_config.NumberColumn(format="₱%.2f"),
            "Sales Amount": st.column_config.NumberColumn(format="₱%.2f"),
        },
    )

    chart_frame = pd.DataFrame(
        {"Units Sold": [r["Units Sold"] for r in product_rows]},
        index=[r["Product"] for r in product_rows],
    )
    try:
        st.bar_chart(chart_frame, color="#E5484D")
    except TypeError:
        st.bar_chart(chart_frame)

    # ---- item-by-item detail ----
    st.subheader("Item-by-Item Sales Detail")
    st.caption("Every product line from every receipt, newest first.")

    time_format = "%I:%M %p" if not show_all else "%b %d, %I:%M %p"
    st.dataframe(
        [
            {
                "Time": l["time"].strftime(time_format) if l["time"] else l["sale_datetime"][:16],
                "Receipt #": l["sale_id"],
                "Product": l["product"],
                "Category": l["category"],
                "Qty Sold": l["quantity"],
                "Unit Price": l["unit_price"],
                "Subtotal": l["subtotal"],
                "Cashier": l["cashier"],
                "Payment": l["payment"],
            }
            for l in lines
        ],
        use_container_width=True,
        hide_index=True,
        column_config={
            "Qty Sold": st.column_config.NumberColumn(format="%d"),
            "Unit Price": st.column_config.NumberColumn(format="₱%.2f"),
            "Subtotal": st.column_config.NumberColumn(format="₱%.2f"),
        },
    )
    st.caption(
        f"{len(lines):,} item line(s) in {transactions:,} transaction(s). "
        f"Totals: {total_units:,} pieces, {money(total_sales)}."
    )


def save_prepared_quantity(widget_key, date_iso, category_id, product_id, prep_id, sold, saved, name):
    """on_change handler for the Prepare Stock rows: saves one product's quantity immediately."""
    new_qty = int(st.session_state.get(widget_key) or 0)
    if new_qty == saved:
        return
    try:
        if new_qty < sold:
            raise ValueError(
                f"{name}: {sold} piece(s) were already sold, so the prepared quantity "
                f"can't go below {sold}."
            )
        if prep_id is None:
            if new_qty > 0:
                get_db().table("preparations").insert({
                    "category_id": category_id,
                    "product_id": product_id,
                    "prepared_date": date_iso,
                    "quantity_prepared": new_qty,
                    "recorded_by": st.session_state["user"]["user_id"],
                }).execute()
        elif new_qty == 0:
            ok, message = delete_row(
                "preparations", {"preparation_id": prep_id}, "the preparation record"
            )
            if not ok:
                raise ValueError(message)
        else:
            get_db().table("preparations").update(
                {"quantity_prepared": new_qty}
            ).eq("preparation_id", prep_id).execute()
        flash(
            f"{name}: prepared quantity "
            + ("cleared." if new_qty == 0 else f"saved as {new_qty}.")
        )
    except Exception as exc:
        st.session_state[widget_key] = saved
        flash_error(f"Could not save {name}: {exc}")


def page_header(section, heading, subtitle):
    """section = the sidebar group (and step) this page lives under, e.g. "DAILY ROUTINE · STEP 1 OF 3"."""
    st.markdown(f'<div class="eyebrow">{html.escape(section.upper())}</div>', unsafe_allow_html=True)
    st.title(heading)
    st.caption(subtitle)
    flash_message = st.session_state.pop("flash", None)
    if flash_message:
        st.success(flash_message)
    flash_problem = st.session_state.pop("flash_error", None)
    if flash_problem:
        st.error(flash_problem)


def require_owner(user):
    if user.get("role") != "owner":
        st.warning("This section is available to the owner only.")
        return False
    return True


def flash_error(message):
    """Show an error message on the next page load (used by on_change callbacks)."""
    st.session_state["flash_error"] = message


def flash(message):
    """Show a success message on the next page load (survives st.rerun())."""
    st.session_state["flash"] = message


def delete_row(table, match, what):
    """Delete the row(s) matching `match` ({column: value}). Returns (ok, error_message).

    Supabase row-level security can block a delete WITHOUT raising an error (it just
    returns no rows), so an empty result is reported instead of a false "deleted".
    """
    q = get_db().table(table).delete()
    for column, value in match.items():
        q = q.eq(column, value)
    try:
        result = q.execute()
    except Exception as exc:
        return False, f"Could not delete {what}: {exc}"
    if not (result.data or []):
        return False, (
            f"Nothing was deleted. The record may already be gone, or the delete policy "
            f"(row-level security) on the '{table}' table in Supabase does not allow it."
        )
    return True, ""


def delete_panel(key, heading, options, caption):
    """Collapsed 'delete' section: pick a record, tick the box, press Delete.

    options = {label shown to the user: value}. Returns the chosen value only on the
    run where Delete is pressed with the box ticked, otherwise None.
    """
    with st.expander(f"🗑️ {heading}", expanded=False):
        st.caption(caption)
        labels = list(options.keys())
        if len(labels) == 1:
            label = labels[0]
            st.markdown(f"**{label}**")
        else:
            label = st.selectbox("Choose a record", labels, key=f"{key}_pick")
        sure = st.checkbox("Yes, delete this permanently", key=f"{key}_sure")
        clicked = st.button("Delete", key=f"{key}_btn", disabled=not sure)
    if clicked and sure:
        # un-tick so the next record isn't pre-confirmed after the rerun
        st.session_state.pop(f"{key}_sure", None)
        return options[label]
    return None


def go_to_page(page_name):
    st.session_state["selected_page"] = page_name


def load_profile(auth_user_id):
    result = (
        get_db().table("app_user")
        .select("*")
        .eq("user_id", str(auth_user_id))
        .limit(1)
        .execute()
    )
    profiles = result.data or []
    if not profiles:
        return None
    profile = profiles[0]
    return profile if profile.get("is_active") else None


# ------------------------- LOGIN -------------------------

@functools.lru_cache(maxsize=None)
def file_to_data_uri(path_str, mime_type):
    file_path = Path(path_str)
    if not file_path.exists():
        return None
    encoded = base64.b64encode(file_path.read_bytes()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def _find_logo_uri():
    """Use assets/logo.png; fall back to the old assets/logo.ico if the PNG is missing."""
    for name, mime in (("assets/logo.png", "image/png"), ("assets/logo.ico", "image/x-icon")):
        uri = file_to_data_uri(name, mime)
        if uri:
            return uri
    return None


LOGO_URI = _find_logo_uri()


def logo_img(css_class="brand-logo", height=34):
    """Inline <img> for the SizzleLog logo; empty string if the file is missing."""
    if not LOGO_URI:
        return ""
    return f'<img src="{LOGO_URI}" alt="SizzleLog" class="{css_class}" style="height:{height}px" />'


if "user" not in st.session_state:
    bg_uri = None
    for _name, _mime in (
        ("login-bg.jpg", "image/jpeg"),
        ("login-bg.jpeg", "image/jpeg"),
        ("login-bg.png", "image/png"),
        ("login-bg.webp", "image/webp"),
    ):
        bg_uri = file_to_data_uri(f"assets/{_name}", _mime)
        if bg_uri:
            break
    logo_uri = LOGO_URI

    if bg_uri:
        st.markdown(
            f"""
            <style>
            .stApp {{
                background:
                    linear-gradient(rgba(60, 14, 18, 0.42), rgba(18, 5, 7, 0.72)),
                    url("{bg_uri}") center center / cover no-repeat fixed;
            }}
            header[data-testid="stHeader"] {{
                background: transparent !important;
            }}
            [data-testid="stToolbar"] {{
                display: none;
            }}
            </style>
            """,
            unsafe_allow_html=True,
        )

    logo_html = (
        f'<img src="{logo_uri}" alt="SizzleLog Logo" class="login-logo-img" />'
        if logo_uri
        else ""
    )

    left, center, right = st.columns([1, 1.2, 1])
    with center:
        with st.form("login_form"):
            st.markdown(
                f"""
                <div class="login-head">
                    {logo_html}
                    <h2 class="login-title">Login</h2>
                    <p class="login-sub">Welcome back to SizzleLog</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
            email = st.text_input("Email", placeholder="you@example.com")
            password = st.text_input("Password", type="password", placeholder="Enter your password")
            submitted = st.form_submit_button("Login", type="primary", use_container_width=True)
            st.markdown(
                """
                <div class="login-foot">
                    Forgot your password? Ask the owner to reset it.<br>
                    Accounts are created by the owner.
                </div>
                """,
                unsafe_allow_html=True,
            )

        if submitted:
            if not email.strip() or not password:
                st.warning("Please enter your email and password.")
            else:
                try:
                    auth_response = base_supabase.auth.sign_in_with_password(
                        {"email": email.strip(), "password": password}
                    )
                    auth_user = auth_response.user
                    if not auth_user:
                        st.error("Login failed. Check your email and password.")
                    else:
                        # Keep this authenticated client for all subsequent queries.
                        st.session_state["supabase"] = base_supabase
                        profile = load_profile(auth_user.id)
                        if not profile:
                            base_supabase.auth.sign_out()
                            st.session_state.pop("supabase", None)
                            st.error("No active SizzleLog profile was found for this account. Ask the owner to check app_user.")
                        else:
                            st.session_state["user"] = profile
                            st.rerun()
                except Exception as exc:
                    st.error(f"Login error: {exc}")

    st.stop()

# ------------------------- AUTHENTICATED APP -------------------------

user = st.session_state["user"]
db = get_db()

st.markdown(PANEL_CSS, unsafe_allow_html=True)

# Restore the authenticated client reference on every Streamlit rerun.
# The object stored after sign-in carries the Supabase auth session.
st.session_state["supabase"] = db

if not user.get("is_active", False):
    st.error("This account is inactive. Contact the owner.")
    st.session_state.clear()
    st.rerun()

role = user.get("role", "")
display_name = user.get("full_name") or user.get("username") or "SizzleLog User"


# ------------------------- SIDEBAR & NAVIGATION -------------------------
if role == "owner":
    NAV_GROUPS = [
        ("HOME", ["Overview"]),
        ("DAILY ROUTINE", ["Preparation", "Sales", "Leftovers", "Expenses"]),
        ("MANAGE", ["Products", "User Management"]),
    ]
else:
    NAV_GROUPS = [
        ("CASHIER", ["Sales", "My Transactions"]),
    ]

pages = [p for _, group_pages in NAV_GROUPS for p in group_pages]

# Reset navigation if the previous page is not allowed
if st.session_state.get("selected_page") not in pages:
    st.session_state["selected_page"] = pages[0]

# ------------------------- NAVIGATION -------------------------

PAGE_TITLES = {
    "Overview": "Overview",
    "Preparation": "Prepare Stock",
    "Sales": "Sales" if role == "owner" else "Sales Counter",
    "Leftovers": "Close Stock",
    "Expenses": "Expenses",
    "Products": "Products",
    "User Management": "Users",
    "My Transactions": "My Transactions",
}

ROUTINE_STEPS = {"Preparation": 1, "Sales": 2, "Leftovers": 3}


def nav_label(page_name):
    return PAGE_TITLES[page_name]


NAV_ICONS = {
    "Overview": ":material/dashboard:",
    "Sales": ":material/point_of_sale:",
    "Preparation": ":material/local_fire_department:",
    "Leftovers": ":material/inventory_2:",
    "Expenses": ":material/payments:",
    "Products": ":material/sell:",
    "User Management": ":material/group:",
    "My Transactions": ":material/receipt_long:",
}


def sidebar_button(label, icon=None, **kwargs):
    """st.button with an optional Material icon; falls back if this Streamlit is too old."""
    try:
        return st.button(label, icon=icon, **kwargs)
    except TypeError:
        return st.button(label, **kwargs)


# Sidebar
with st.sidebar:
    st.markdown(
        f"""
        <div class="side-brand">
          <div class="side-brand-name">{logo_img("side-logo", 60)}<span>SIZZLELOG</span></div>
          <div class="side-brand-tag">BBQ BUSINESS MANAGEMENT</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    safe_name = html.escape(str(display_name))
    initial = html.escape(str(display_name).strip()[:1].upper() or "S")
    st.markdown(
        f"""
        <div class="side-user">
          <div class="side-avatar">{initial}</div>
          <div>
            <div class="side-user-name">{safe_name}</div>
            <div class="side-user-role">{html.escape(role.upper())}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.divider()

    for group_label, group_pages in NAV_GROUPS:
        st.markdown(f'<div class="side-nav-label">{group_label}</div>', unsafe_allow_html=True)
        for page_name in group_pages:
            sidebar_button(
                nav_label(page_name),
                icon=NAV_ICONS.get(page_name),
                key=f"nav_{page_name}",
                use_container_width=True,
                type="primary" if st.session_state.get("selected_page") == page_name else "secondary",
                on_click=go_to_page,
                args=(page_name,),
            )

    selected_page = st.session_state["selected_page"]

    st.divider()

    if sidebar_button("Log Out", icon=":material/logout:", key="nav_logout", use_container_width=True):
        try:
            db.auth.sign_out()
        except Exception:
            pass

        st.session_state.clear()
        st.rerun()



# ------------------------- OVERVIEW -------------------------

if selected_page == "Overview":
    today = date.today()
    yesterday = today - timedelta(days=1)
    current_hour = datetime.now().hour

    # ------------------------- LOAD DAILY RECORDS -------------------------

    try:
        # Sales
        sales_today = get_sales_for_date(today)
        sales_yesterday = get_sales_for_date(yesterday)

        today_sale_ids = {
            int(s["sale_id"]) for s in sales_today
        }
        yesterday_sale_ids = {
            int(s["sale_id"]) for s in sales_yesterday
        }

        all_sale_items = query_rows(
            "sale_items",
            "sale_id, preparation_id, quantity_sold, unit_price",
        )

        today_items = [
            item for item in all_sale_items
            if int(item["sale_id"]) in today_sale_ids
        ]

        yesterday_items = [
            item for item in all_sale_items
            if int(item["sale_id"]) in yesterday_sale_ids
        ]

        today_sales_total = sum(
            int(item["quantity_sold"]) * as_float(item["unit_price"])
            for item in today_items
        )

        yesterday_sales_total = sum(
            int(item["quantity_sold"]) * as_float(item["unit_price"])
            for item in yesterday_items
        )

        today_pieces_sold = sum(
            int(item["quantity_sold"]) for item in today_items
        )

        # Expenses
        all_expenses = query_rows(
            "expenses",
            "expense_id, expense_date, amount",
        )

        expenses_today = [
            e for e in all_expenses
            if str(e["expense_date"]) == today.isoformat()
        ]

        expenses_yesterday = [
            e for e in all_expenses
            if str(e["expense_date"]) == yesterday.isoformat()
        ]

        today_expense_total = sum(
            as_float(e["amount"]) for e in expenses_today
        )

        yesterday_expense_total = sum(
            as_float(e["amount"]) for e in expenses_yesterday
        )

        # Preparation
        preparations = get_today_preparations()
        products = get_products()

        prepared_total = sum(
            int(p["quantity_prepared"]) for p in preparations
        )

        prepared_product_count = len(preparations)

        # Stock movement
        preparation_ids = [
            int(p["preparation_id"]) for p in preparations
        ]

        sold_map = get_sold_quantities(preparation_ids)
        leftover_map = get_leftover_map(preparation_ids)

        total_sold = sum(sold_map.values())

        expected_remaining = sum(
            max(
                int(p["quantity_prepared"])
                - sold_map.get(int(p["preparation_id"]), 0),
                0,
            )
            for p in preparations
        )

        actual_leftovers = sum(
            int(item["quantity_remaining"])
            for item in leftover_map.values()
        )

        closed_products = len(leftover_map)
        products_to_close = len(preparations)

        # Business status
        if not preparations:
            business_status = "Preparation Not Started"
            status_message = (
                "Record today's prepared stock before opening the sales counter."
            )
        elif closed_products == products_to_close:
            business_status = "Stock Closed for the Day"
            status_message = (
                "All prepared products have recorded closing quantities."
            )
        elif current_hour < 18:
            business_status = "Pre-Opening"
            status_message = (
                "Review today's preparation and make sure stock is ready "
                "before the 6:00 PM opening."
            )
        else:
            business_status = "Business Hours"
            status_message = (
                "Monitor sales and available stock. Record actual leftovers "
                "when the business closes."
            )

    except Exception as exc:
        st.error(f"Dashboard could not load all business records: {exc}")
        st.stop()

    # The single most useful thing to do right now.
    if not preparations:
        next_page = "Preparation"
        next_label = "Record today's prepared stock"
    elif closed_products == products_to_close:
        next_page = "Expenses"
        next_label = "Record today's expenses"
    else:
        next_page = "Sales"
        next_label = "Review today's sales"

    # ------------------------- HERO -------------------------

    _hour = datetime.now().hour
    greeting = "Good morning" if _hour < 12 else "Good afternoon" if _hour < 18 else "Good evening"
    hero_name = html.escape(str(display_name))

    st.markdown(
        f"""
        <div class="hero">
            <p class="hero-greet">{greeting}, <strong>{hero_name}</strong></p>
            <span class="hero-pill">DAILY BUSINESS OVERVIEW</span>
            <h1 class="hero-title">{logo_img("hero-logo", 88)}<span>SIZZLELOG</span></h1>
            <p>{today.strftime("%A, %B %d, %Y")}</p>
            <p class="hero-status">
                <strong>{business_status}</strong>
                <br>{status_message}
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.button(
        f"Next step: {next_label}  →",
        key="overview_next_step",
        type="primary",
        on_click=go_to_page,
        args=(next_page,),
    )

    # ------------------------- BUSINESS SUMMARY (LIVE) -------------------------

    st.subheader("Business Summary")
    st.caption(
        "These figures come straight from your records and refresh by themselves. "
        "Prepare stock, make a sale or log an expense and they update."
    )
    business_summary()

    st.divider()

    # ------------------------- TODAY'S ROUTINE -------------------------

    st.subheader("Today's Routine")
    st.caption("Work through these in order. Each card shows where that step stands.")

    prep_col, sales_col, closing_col = st.columns(3)

    with prep_col:
        with st.container(border=True):
            st.markdown("### 1 · Prepare Stock")
            st.metric("Products Prepared", f"{prepared_product_count}")
            st.metric("Total Pieces", f"{prepared_total:,}")

            if not preparations:
                st.warning("Not started yet.")
            else:
                st.success("Recorded for today.")

            st.button(
                f"Go to {PAGE_TITLES['Preparation']}",
                key="overview_preparation",
                use_container_width=True,
                on_click=go_to_page,
                args=("Preparation",),
            )

    with sales_col:
        with st.container(border=True):
            st.markdown("### 2 · Sales Counter")
            st.metric("Pieces Sold", f"{total_sold:,}")
            st.metric("Expected Remaining", f"{expected_remaining:,}")

            if current_hour < 18:
                st.info("Sales counter opens at 6:00 PM.")
            else:
                st.success("Sales are open.")

            st.button(
                f"Go to {PAGE_TITLES['Sales']}",
                key="overview_sales",
                use_container_width=True,
                on_click=go_to_page,
                args=("Sales",),
            )

    with closing_col:
        with st.container(border=True):
            st.markdown("### 3 · Close Stock")
            st.metric("Products Closed", f"{closed_products} / {products_to_close}")
            st.metric(
                "Actual Leftovers",
                f"{actual_leftovers:,}" if leftover_map else "Not recorded yet",
            )

            if not preparations:
                st.warning("Waiting for preparation records.")
            elif closed_products == products_to_close:
                st.success("All products have closing records.")
            else:
                st.warning(
                    f"{products_to_close - closed_products} product(s) "
                    "still need closing."
                )

            st.button(
                f"Go to {PAGE_TITLES['Leftovers']}",
                key="overview_closing",
                use_container_width=True,
                on_click=go_to_page,
                args=("Leftovers",),
            )

    st.button(
        "💰 Record an Expense",
        key="overview_expenses",
        on_click=go_to_page,
        args=("Expenses",),
    )

    st.divider()

    # ------------------------- YESTERDAY'S PERFORMANCE -------------------------

    with st.expander(
        f"📊 Previous Business Day ({yesterday.strftime('%B %d, %Y')})",
        expanded=False,
    ):
        st.caption(
            "These are yesterday's records. They are shown separately "
            "and are not included in today's totals."
        )

        y1, y2, y3, y4 = st.columns(4)

        y1.metric(
            "Yesterday's Sales",
            money(yesterday_sales_total),
        )

        y2.metric(
            "Yesterday's Expenses",
            money(yesterday_expense_total),
        )

        y3.metric(
            "Transactions",
            f"{len(sales_yesterday):,}",
        )

        y4.metric(
            "Pieces Sold",
            f"{sum(int(item['quantity_sold']) for item in yesterday_items):,}",
        )

        if not sales_yesterday and not expenses_yesterday:
            st.info("No sales or expenses were recorded yesterday.")



# ------------------------- PRODUCTS -------------------------

elif selected_page == "Products":
    page_header(
        "MANAGE",
        "Products",
        "Manage registered products, selling prices, and reference information.",
    )

    if not require_owner(user):
        st.stop()

    try:
        categories = get_categories()
        products = get_products()

    except Exception as exc:
        st.error(f"Could not load products or categories: {exc}")
        st.stop()

    if not categories:
        st.error(
            "No product categories are visible. "
            "Check the item_categories SELECT policy in Supabase."
        )
        st.stop()

    category_lookup = {
        int(c["category_id"]): c["category_name"]
        for c in categories
    }

    category_name_to_id = {
        c["category_name"]: int(c["category_id"])
        for c in categories
    }

    # ------------------------- PRODUCT SUMMARY -------------------------

    col1, col2 = st.columns(2)

    col1.metric("Registered Products", len(products))
    col2.metric("Product Categories", len(categories))

    st.caption(
        "Selling prices are managed here by the owner. "
        "Reference leftover quantities are not actual daily stock."
    )

    st.divider()

    # ------------------------- ADD PRODUCT -------------------------

    with st.expander("➕ Add a Product", expanded=False):

        with st.form("add_product_form", clear_on_submit=True):

            chosen_category = st.selectbox(
                "Product Category",
                list(category_name_to_id.keys()),
            )

            item_name = st.text_input(
                "Product Name",
                placeholder="Enter the product name",
            )

            price = st.number_input(
                "Selling Price (₱)",
                min_value=0.01,
                value=1.00,
                step=1.00,
                format="%.2f",
            )

            st.markdown("**Leftover Reference Quantities**")

            st.caption(
                "These are reference figures only. "
                "Actual daily leftovers are recorded under Close Stock."
            )

            busy_leftovers = st.number_input(
                "Busy Day Leftovers (Reference)",
                min_value=0,
                value=0,
                step=1,
            )

            usual_leftovers = st.number_input(
                "Non-Busy Day Leftovers (Reference)",
                min_value=0,
                value=0,
                step=1,
            )

            status = st.text_input(
                "Sales Status",
                value="Regular",
                help="Example: Regular, Always Sold Out, Moderate Leftover / Saved.",
            )

            add_product = st.form_submit_button(
                "Save New Product",
                type="primary",
                use_container_width=True,
            )

        if add_product:

            if not item_name.strip():
                st.warning("Enter a product name.")

            elif price <= 0:
                st.warning("Selling price must be greater than zero.")

            else:
                try:
                    category_id = category_name_to_id[chosen_category]

                    same_category_ids = [
                        int(p["product_id"])
                        for p in products
                        if int(p["category_id"]) == category_id
                    ]

                    next_id = max(same_category_ids, default=0) + 1

                    db.table("products").insert({
                        "product_id": next_id,
                        "category_id": category_id,
                        "item_description": item_name.strip(),
                        "selling_price": price,
                        "busy_day_leftovers_qty": int(busy_leftovers),
                        "non_busy_day_leftovers_qty": int(usual_leftovers),
                        "sales_status": status.strip() or "Regular",
                    }).execute()

                    st.success("New product added successfully.")
                    st.rerun()

                except Exception as exc:
                    st.error(f"Could not add product: {exc}")

    # ------------------------- PRODUCT LIST -------------------------

    st.subheader("Product List")

    search = st.text_input(
        "Search Products",
        placeholder="Type a product name...",
    )

    filter_options = ["All Categories"] + list(category_lookup.values())

    chosen_filter = st.selectbox(
        "Filter by Category",
        filter_options,
    )

    filtered = products.copy()

    if search.strip():
        filtered = [
            p for p in filtered
            if search.lower() in (
                p.get("item_description") or ""
            ).lower()
        ]

    if chosen_filter != "All Categories":
        selected_category_id = next(
            key for key, value in category_lookup.items()
            if value == chosen_filter
        )

        filtered = [
            p for p in filtered
            if int(p["category_id"]) == selected_category_id
        ]

    if not filtered:
        st.info("No products match your search.")

    else:
        display_rows = []

        for p in filtered:
            display_rows.append({
                "Category": category_lookup.get(
                    int(p["category_id"]),
                    "Unknown",
                ),
                "Product": p["item_description"],
                "Selling Price": money(p["selling_price"]),
                "Busy Day Leftovers (Reference)": int(
                    p.get("busy_day_leftovers_qty") or 0
                ),
                "Non-Busy Day Leftovers (Reference)": int(
                    p.get("non_busy_day_leftovers_qty") or 0
                ),
                "Sales Status": p.get("sales_status") or "",
            })

        st.dataframe(
            display_rows,
            use_container_width=True,
            hide_index=True,
        )

    st.caption(f"Showing {len(filtered)} of {len(products)} registered products.")

    st.divider()

    # ------------------------- EDIT PRODUCT -------------------------

    st.subheader("Edit Product")

    if not products:
        st.info("No products are available to edit.")

    else:
        product_options = {
            (
                f'{p["item_description"]} | '
                f'{category_lookup.get(int(p["category_id"]), "Unknown")} '
                f'(ID {p["product_id"]})'
            ): (
                int(p["category_id"]),
                int(p["product_id"]),
                p,
            )
            for p in products
        }

        chosen_product_label = st.selectbox(
            "Choose a Product to Edit",
            list(product_options.keys()),
        )

        cat_id, prod_id, chosen_product = product_options[
            chosen_product_label
        ]

        st.caption(
            "Changes made here affect the product's registered information. "
            "Daily preparation and stock closing records are separate."
        )

        with st.form("edit_product_form"):

            edit_name = st.text_input(
                "Product Name",
                value=chosen_product["item_description"],
            )

            edit_price = st.number_input(
                "Selling Price (₱)",
                min_value=0.01,
                value=max(
                    as_float(chosen_product["selling_price"]),
                    0.01,
                ),
                step=1.00,
                format="%.2f",
            )

            st.markdown("**Leftover Reference Quantities**")

            edit_busy_leftovers = st.number_input(
                "Busy Day Leftovers (Reference)",
                min_value=0,
                value=int(
                    chosen_product.get("busy_day_leftovers_qty") or 0
                ),
                step=1,
            )

            edit_leftovers = st.number_input(
                "Non-Busy Day Leftovers (Reference)",
                min_value=0,
                value=int(
                    chosen_product.get("non_busy_day_leftovers_qty") or 0
                ),
                step=1,
            )

            edit_status = st.text_input(
                "Sales Status",
                value=chosen_product.get("sales_status") or "",
            )

            save_edit = st.form_submit_button(
                "Update Product",
                type="primary",
                use_container_width=True,
            )

        if save_edit:

            if not edit_name.strip():
                st.warning("Product name cannot be empty.")

            elif edit_price <= 0:
                st.warning("Selling price must be greater than zero.")

            else:
                try:
                    db.table("products").update({
                        "item_description": edit_name.strip(),
                        "selling_price": edit_price,
                        "busy_day_leftovers_qty": int(
                            edit_busy_leftovers
                        ),
                        "non_busy_day_leftovers_qty": int(
                            edit_leftovers
                        ),
                        "sales_status": edit_status.strip(),
                    }).eq(
                        "category_id", cat_id
                    ).eq(
                        "product_id", prod_id
                    ).execute()

                    st.success("Product information updated.")
                    st.rerun()

                except Exception as exc:
                    st.error(f"Could not update product: {exc}")

        picked = delete_panel(
            "del_product",
            "Delete this product",
            {f'{chosen_product["item_description"]} (ID {prod_id})': (cat_id, prod_id)},
            "Only products that were never prepared can be deleted. Once a product has "
            "preparation records, deleting it would break your history.",
        )
        if picked:
            try:
                used = query_rows(
                    "preparations", "preparation_id",
                    category_id=picked[0], product_id=picked[1],
                )
            except Exception as exc:
                st.error(f"Could not check this product before deleting: {exc}")
            else:
                if used:
                    st.error(
                        f"This product has {len(used)} preparation record(s) in your history, "
                        "so it can't be deleted. Keep it, or edit its details above."
                    )
                else:
                    ok, message = delete_row(
                        "products", {"category_id": picked[0], "product_id": picked[1]}, "the product"
                    )
                    if ok:
                        flash("Product deleted.")
                        st.rerun()
                    else:
                        st.error(message)



# ------------------------- SALES POS -------------------------

elif selected_page == "Sales":
    # The owner lands on the item-level breakdown; the counter is one click away.
    owner_view = (
        st.session_state.get("owner_sales_view", "Sales Breakdown")
        if role == "owner" else "Sales Counter"
    )

    if owner_view == "Sales Breakdown":
        page_header(
            "DAILY ROUTINE · STEP 2 OF 3",
            "Sales",
            "See which products were sold, how many units, at what price and when.",
        )
    else:
        page_header(
            "DAILY ROUTINE · STEP 2 OF 3" if role == "owner" else "CASHIER",
            "Sales Counter",
            "Create customer orders, collect cash, and issue receipts.",
        )

    if role == "owner":
        st.radio(
            "View",
            ["Sales Breakdown", "Sales Counter"],
            horizontal=True,
            key="owner_sales_view",
            label_visibility="collapsed",
        )
        if owner_view == "Sales Breakdown":
            render_sales_breakdown()
            st.stop()

    # Load today's stock and sales information
    try:
        preparations = get_today_preparations()
        products_map = get_product_map()
        categories = get_categories()

        category_map = {
            int(c["category_id"]): c["category_name"]
            for c in categories
        }

        prep_ids = [int(p["preparation_id"]) for p in preparations]
        sold_map = get_sold_quantities(prep_ids)
        leftover_map = get_leftover_map(prep_ids)

    except Exception as exc:
        st.error(f"Could not load today's POS information: {exc}")
        st.stop()

    # Calculate currently available products
    available_items = []

    for prep in preparations:
        prep_id = int(prep["preparation_id"])
        key = (int(prep["category_id"]), int(prep["product_id"]))

        product = products_map.get(key)

        if not product or prep_id in leftover_map:
            continue

        prepared_qty = int(prep["quantity_prepared"])
        sold_qty = sold_map.get(prep_id, 0)
        available = prepared_qty - sold_qty

        if available > 0:
            available_items.append({
                "preparation_id": prep_id,
                "category": category_map.get(key[0], "Uncategorized"),
                "name": product["item_description"],
                "price": as_float(product["selling_price"]),
                "available": available,
            })

    # Initialize cart
    if "cart" not in st.session_state:
        st.session_state["cart"] = {}

    cart = st.session_state["cart"]

    # ------------------------- COUNTER STATUS -------------------------

    total_available = sum(item["available"] for item in available_items)

    c1, c2, c3 = st.columns(3)

    c1.metric("Products Available", len(available_items))
    c2.metric("Pieces Available", f"{total_available:,}")
    c3.metric("Items in Cart", sum(
        int(item["quantity"]) for item in cart.values()
    ))

    st.divider()

    
 
    products_col, order_col = st.columns([3, 2], gap="large")

    with products_col:
        # ------------------------- ADD PRODUCTS -------------------------

        st.subheader("Select Products")
        st.caption("Select a category, then tap a product card to add one piece.")

        if not available_items:
            st.info(
                "No prepared products are currently available. "
                "The owner must record today's preparation first."
            )
            if role == "owner":
                st.button(
                    f"Go to {PAGE_TITLES['Preparation']}",
                    key="sales_to_prep",
                    type="primary",
                    on_click=go_to_page,
                    args=("Preparation",),
                )

        else:
            # ---------------- CATEGORY BUTTONS ----------------

            available_categories = sorted({
                item["category"] for item in available_items
            })

            category_options = ["All Products"] + available_categories

            if "pos_category_filter" not in st.session_state:
                st.session_state["pos_category_filter"] = "All Products"

            selected_category = st.session_state["pos_category_filter"]

            category_columns = st.columns(3)

            for index, category in enumerate(category_options):
                with category_columns[index % 3]:
                    is_selected = selected_category == category

                    if st.button(
                        category,
                        key=f"category_btn_{index}",
                        use_container_width=True,
                        type="primary" if is_selected else "secondary",
                    ):
                        st.session_state["pos_category_filter"] = category
                        st.rerun()

            selected_category = st.session_state["pos_category_filter"]

            # ---------------- FILTER PRODUCTS ----------------

            if selected_category == "All Products":
                displayed_items = available_items
            else:
                displayed_items = [
                    item for item in available_items
                    if item["category"] == selected_category
                ]

            st.divider()

            # ---------------- PRODUCT CARDS ----------------

            product_columns = st.columns(3)

            for index, item in enumerate(displayed_items):
                prep_id = item["preparation_id"]

                current_cart_qty = int(
                    cart.get(prep_id, {}).get("quantity", 0)
                )

                remaining_for_cart = (
                    item["available"] - current_cart_qty
                )

                column = product_columns[index % 3]

                with column:
                    st.markdown(
                        f"""
                        <div class="sales-product-card">
                            <div class="sales-product-name">{item["name"]}</div>
                            <div class="sales-product-price">{money(item["price"])}</div>
                            <div class="sales-product-stock">{item["available"]} pieces available</div>
                            <div class="sales-product-cart">In order: {current_cart_qty}</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                    if st.button(
                        "＋ ADD TO ORDER" if remaining_for_cart > 0
                        else "STOCK LIMIT REACHED",
                        key=f"add_product_{prep_id}",
                        use_container_width=True,
                        type="primary",
                        disabled=remaining_for_cart <= 0,
                    ):
                        cart[prep_id] = {
                            "preparation_id": prep_id,
                            "name": item["name"],
                            "category": item["category"],
                            "unit_price": item["price"],
                            "quantity": current_cart_qty + 1,
                        }

                        st.session_state["cart"] = cart
                        st.rerun()

            st.caption(
                "Each click adds one piece. Adjust quantities in Current Order."
            )

    with order_col:
        # ------------------------- CURRENT ORDER -------------------------

        st.subheader("Current Order")

        cart = st.session_state.get("cart", {})

        if not cart:
            st.info("Your order is empty. Add products to begin.")
        else:
            cart_total = sum(
                int(item["quantity"]) * float(item["unit_price"])
                for item in cart.values()
            )

            st.caption("Change quantities or remove items before checkout.")

            for prep_id, item in list(cart.items()):
                current_qty = int(item["quantity"])

                # Show current stock availability
                current_stock = next(
                    (
                        p["available"]
                        for p in available_items
                        if p["preparation_id"] == prep_id
                    ),
                    0,
                )

                with st.container(border=True):
                    col_name, col_qty, col_remove = st.columns([2.4, 1.6, 0.9])

                    with col_qty:
                        new_qty = st.number_input(
                            "Qty",
                            min_value=0,
                            max_value=max(current_qty, current_stock, 1),
                            value=current_qty,
                            step=1,
                            key=f"cart_qty_{prep_id}",
                        )

                    with col_name:
                        st.markdown(f"**{item['name']}**")
                        st.caption(
                            f'{money(item["unit_price"])} each · '
                            f'{money(new_qty * float(item["unit_price"]))}'
                        )

                    with col_remove:
                        st.caption("Remove")
                        remove_item = st.button(
                            "✕",
                            key=f"remove_cart_{prep_id}",
                            use_container_width=True,
                        )

                if remove_item or new_qty == 0:
                    cart.pop(prep_id, None)
                    st.session_state.pop(f"cart_qty_{prep_id}", None)
                    st.session_state["cart"] = cart
                    st.rerun()

                elif new_qty != current_qty:
                    cart[prep_id]["quantity"] = int(new_qty)
                    st.session_state["cart"] = cart
                    st.rerun()

                if current_qty > current_stock:
                    st.warning(
                        f'{item["name"]}: Only {current_stock} piece(s) '
                        "are currently available. Adjust the quantity."
                    )

            # Recalculate total after any cart changes
            cart = st.session_state.get("cart", {})

            cart_total = sum(
                int(item["quantity"]) * float(item["unit_price"])
                for item in cart.values()
            )

            st.divider()

            st.markdown("### ORDER TOTAL")
            st.markdown(
                f'<h1 style="color:#B82025;font-size:38px">'
                f'{money(cart_total)}</h1>',
                unsafe_allow_html=True,
            )

            if st.button("Clear Entire Order", use_container_width=True):
                for prep_id in cart:
                    st.session_state.pop(f"cart_qty_{prep_id}", None)

                st.session_state["cart"] = {}
                st.rerun()

            # ------------------------- PAYMENT -------------------------

            st.divider()
            st.subheader("Cash Payment")

            with st.form("checkout_form"):
                cash_received = st.number_input(
                    "Cash Received (₱)",
                    min_value=0.0,
                    value=float(cart_total),
                    step=1.0,
                    format="%.2f",
                )

                change_preview = cash_received - cart_total

                p1, p2 = st.columns(2)
                p1.metric("Amount Due", money(cart_total))
                p2.metric(
                    "Change",
                    money(max(change_preview, 0)),
                )

                checkout = st.form_submit_button(
                    "COMPLETE CASH SALE",
                    type="primary",
                    use_container_width=True,
                )

            if checkout:
                if not cart:
                    st.warning("The order is empty.")
                elif cash_received < cart_total:
                    st.error("Cash received is not enough to cover the order.")
                else:
                    try:
                        # Recheck stock immediately before checkout
                        current_sold = get_sold_quantities(list(cart.keys()))
                        current_leftovers = get_leftover_map(list(cart.keys()))

                        current_preparations = query_rows(
                            "preparations",
                            "preparation_id, quantity_prepared",
                        )

                        preparation_lookup = {
                            int(p["preparation_id"]): int(p["quantity_prepared"])
                            for p in current_preparations
                        }

                        for prep_id, item in cart.items():
                            if prep_id in current_leftovers:
                                raise ValueError(
                                    f'{item["name"]} has already been closed for the day.'
                                )

                            if prep_id not in preparation_lookup:
                                raise ValueError(
                                    f'Preparation record for {item["name"]} was not found.'
                                )

                            remaining = (
                                preparation_lookup[prep_id]
                                - current_sold.get(prep_id, 0)
                            )

                            if int(item["quantity"]) > remaining:
                                raise ValueError(
                                    f'Not enough remaining stock for {item["name"]}. '
                                    f'Only {remaining} piece(s) remain.'
                                )

                        # Prepare receipt details before clearing the cart
                        receipt_items = [
                            {
                                "name": item["name"],
                                "quantity": int(item["quantity"]),
                                "unit_price": float(item["unit_price"]),
                                "subtotal": (
                                    int(item["quantity"])
                                    * float(item["unit_price"])
                                ),
                            }
                            for item in cart.values()
                        ]

                        change = float(cash_received) - cart_total

                        # Create sale header and identify the cashier
                        sale_response = db.table("sales").insert({
                            "recorded_by": user["user_id"],
                            "payment_method": "Cash",
                            "notes": (
                                f"Cash received: {money(cash_received)}; "
                                f"Change: {money(change)}"
                            ),
                        }).execute()

                        sale_rows = sale_response.data or []

                        if not sale_rows:
                            raise RuntimeError(
                                "The sale record was not returned by Supabase."
                            )

                        sale_id = int(sale_rows[0]["sale_id"])

                        # Save individual products under the sale
                        sale_items_payload = [
                            {
                                "sale_id": sale_id,
                                "preparation_id": int(prep_id),
                                "quantity_sold": int(item["quantity"]),
                                "unit_price": float(item["unit_price"]),
                            }
                            for prep_id, item in cart.items()
                        ]

                        db.table("sale_items").insert(
                            sale_items_payload
                        ).execute()

                        # Save receipt for display
                        st.session_state["last_receipt"] = {
                            "sale_id": sale_id,
                            "cashier": display_name,
                            "date": datetime.now().strftime(
                                "%B %d, %Y | %I:%M %p"
                            ),
                            "items": receipt_items,
                            "total": cart_total,
                            "cash": float(cash_received),
                            "change": change,
                        }

                        for prep_id in cart:
                            st.session_state.pop(f"cart_qty_{prep_id}", None)

                        st.session_state["cart"] = {}

                        st.success(
                            f"Sale #{sale_id} recorded successfully!"
                        )
                        st.rerun()

                    except Exception as exc:
                        st.error(f"Could not complete the sale: {exc}")

        # ------------------------- LATEST RECEIPT -------------------------

        receipt = st.session_state.get("last_receipt")

        if receipt:
            st.divider()
            st.subheader("Latest Receipt")

            with st.container(border=True):
                st.markdown(
                    """
                    <div style="text-align:center">
                        <h2 style="margin-bottom:0">__LOGO__ SIZZLELOG</h2>
                        <p style="color:#777;margin-top:0">
                            BBQ BUSINESS SALES RECEIPT
                        </p>
                    </div>
                    """.replace("__LOGO__", logo_img("receipt-logo", 48)),
                    unsafe_allow_html=True,
                )

                st.write(f'**Receipt #:** {receipt["sale_id"]}')
                st.write(f'**Date:** {receipt["date"]}')
                st.write(f'**Cashier:** {receipt["cashier"]}')

                st.divider()

                st.dataframe(
                    [
                        {
                            "Item": item["name"],
                            "Qty": item["quantity"],
                            "Price": money(item["unit_price"]),
                            "Subtotal": money(item["subtotal"]),
                        }
                        for item in receipt["items"]
                    ],
                    use_container_width=True,
                    hide_index=True,
                )

                st.divider()

                st.write(f'**TOTAL:** {money(receipt["total"])}')
                st.write(f'**CASH RECEIVED:** {money(receipt["cash"])}')
                st.markdown(
                    f'### CHANGE: {money(receipt["change"])}'
                )

                st.markdown(
                    '<p style="text-align:center;color:#777">'
                    'Thank you for your purchase!'
                    '</p>',
                    unsafe_allow_html=True,
                )

            if st.button("Dismiss Receipt"):
                st.session_state.pop("last_receipt", None)
                st.rerun()


# ------------------------- DAILY STOCK PREPARATION -------------------------

elif selected_page == "Preparation":
    page_header(
        "DAILY ROUTINE · STEP 1 OF 3",
        "Prepare Stock",
        "Set the quantity prepared for every product. Changes save instantly and sync everywhere.",
    )

    if not require_owner(user):
        st.stop()

    try:
        target_date = st.date_input(
            "Business Date",
            value=date.today(),
            key="preparation_date",
        )

        categories = get_categories()
        products = get_products()

        existing_preparations = query_rows(
            "preparations",
            "preparation_id, category_id, product_id, prepared_date, "
            "quantity_prepared, notes",
            prepared_date=target_date.isoformat(),
        )

    except Exception as exc:
        st.error(f"Could not load daily stock information: {exc}")
        st.stop()

    category_names = {
        int(c["category_id"]): c["category_name"]
        for c in categories
    }

    product_map = {
        (int(p["category_id"]), int(p["product_id"])): p
        for p in products
    }

    existing_map = {
        (int(p["category_id"]), int(p["product_id"])): p
        for p in existing_preparations
    }

    if not products:
        st.warning("No products found. Add products first.")
        st.button(
            f"Go to {PAGE_TITLES['Products']}",
            key="prep_to_products",
            on_click=go_to_page,
            args=("Products",),
        )

    else:
        prep_ids_today = [int(p["preparation_id"]) for p in existing_preparations]
        sold_map = get_sold_quantities(prep_ids_today)
        closed_map = get_leftover_map(prep_ids_today)

        # ---- live totals (they change as soon as a quantity is saved) ----
        total_prepared = sum(int(p["quantity_prepared"]) for p in existing_preparations)
        total_sold_today = sum(sold_map.values())

        t1, t2, t3 = st.columns(3)
        t1.metric("Products Prepared", f"{len(existing_preparations)} of {len(products)}")
        t2.metric("Total Pieces Prepared", f"{total_prepared:,}")
        t3.metric("Pieces Sold So Far", f"{total_sold_today:,}")

        st.subheader("Prepared Quantity per Product")
        st.caption(
            "Type the quantity beside each product and press Enter. It saves right away and "
            "updates Sales, Close Stock and Overview. Set 0 to remove a product from today "
            "(only possible before anything is sold)."
        )

        date_iso = target_date.isoformat()
        ordered_products = sorted(
            products,
            key=lambda p: (
                category_names.get(int(p["category_id"]), "~").lower(),
                str(p["item_description"]).lower(),
            ),
        )

        def prep_columns(spec):
            try:
                return st.columns(spec, vertical_alignment="center")
            except TypeError:
                return st.columns(spec)

        head = prep_columns([4, 1.2, 1.6, 2])
        for col, label in zip(head, ["PRODUCT", "SOLD", "STATUS", "QUANTITY PREPARED"]):
            col.markdown(f'<div class="prep-head">{label}</div>', unsafe_allow_html=True)

        current_category = None
        for product in ordered_products:
            category_id = int(product["category_id"])
            product_id = int(product["product_id"])
            category_label = category_names.get(category_id, "Unknown")

            if category_label != current_category:
                current_category = category_label
                st.markdown(
                    f'<div class="prep-cat">{html.escape(category_label)}</div>',
                    unsafe_allow_html=True,
                )

            record = existing_map.get((category_id, product_id))
            prep_id = int(record["preparation_id"]) if record else None
            saved_qty = int(record["quantity_prepared"]) if record else 0
            sold_qty = sold_map.get(prep_id, 0) if prep_id else 0
            is_closed = prep_id in closed_map if prep_id else False
            product_name = str(product["item_description"])

            if is_closed:
                status_html = '<span class="prep-tag closed">Closed</span>'
            elif record:
                status_html = '<span class="prep-tag ready">Prepared</span>'
            else:
                status_html = '<span class="prep-tag none">Not prepared</span>'

            c_name, c_sold, c_status, c_input = prep_columns([4, 1.2, 1.6, 2])
            c_name.markdown(
                f'<div class="prep-name">{html.escape(product_name)}</div>'
                f'<div class="prep-meta">{money(product["selling_price"])} each</div>',
                unsafe_allow_html=True,
            )
            c_sold.markdown(f"**{sold_qty}**")
            c_status.markdown(status_html, unsafe_allow_html=True)

            # The key includes the saved value, so the box always shows what the
            # database holds (even if it was changed from another screen).
            widget_key = f"prepqty_{date_iso}_{category_id}_{product_id}_{saved_qty}"
            c_input.number_input(
                f"Quantity prepared: {product_name}",
                min_value=min(sold_qty, saved_qty),
                value=saved_qty,
                step=1,
                format="%d",
                key=widget_key,
                disabled=is_closed,
                label_visibility="collapsed",
                on_change=save_prepared_quantity,
                args=(
                    widget_key, date_iso, category_id, product_id,
                    prep_id, sold_qty, saved_qty, product_name,
                ),
            )

        if closed_map:
            st.caption(
                f"Closed products are locked. Open {PAGE_TITLES['Leftovers']} and press "
                "Undo closing to change them."
            )

    st.divider()
    st.button(
        f"Next: {PAGE_TITLES['Sales']}  →",
        key="prep_next",
        type="primary",
        on_click=go_to_page,
        args=("Sales",),
    )


# ------------------------- STOCK CLOSING -------------------------

elif selected_page == "Leftovers":
    page_header(
        "DAILY ROUTINE · STEP 3 OF 3",
        "Close Stock",
        "Remaining stock = Prepared − Sold, calculated for every product from your records.",
    )

    if not require_owner(user):
        st.stop()

    try:
        target_date = st.date_input(
            "Business Date",
            value=date.today(),
            key="closing_date",
        )

        prep_rows = query_rows(
            "preparations",
            "preparation_id, category_id, product_id, prepared_date, quantity_prepared",
            prepared_date=target_date.isoformat(),
        )

        product_map = get_product_map()
        categories = get_categories()

        category_names = {
            int(c["category_id"]): c["category_name"]
            for c in categories
        }

        prep_ids = [
            int(p["preparation_id"])
            for p in prep_rows
        ]

        sold_map = get_sold_quantities(prep_ids)
        leftover_map = get_leftover_map(prep_ids)

    except Exception as exc:
        st.error(f"Could not load stock closing information: {exc}")
        st.stop()

    if not prep_rows:
        st.info(
            "No preparation records found for this business date. "
            "Record prepared stock first."
        )
        st.button(
            f"Go to {PAGE_TITLES['Preparation']}",
            key="closing_to_prep",
            type="primary",
            on_click=go_to_page,
            args=("Preparation",),
        )

    else:
        # ------------------------- REMAINING STOCK (PREPARED - SOLD) -------------------------

        ordered_preps = sorted(
            prep_rows,
            key=lambda p: (
                category_names.get(int(p["category_id"]), "~").lower(),
                str(
                    product_map.get(
                        (int(p["category_id"]), int(p["product_id"])), {}
                    ).get("item_description", "")
                ).lower(),
            ),
        )

        stock_rows = []
        pending_close = []

        for prep in ordered_preps:
            prep_id = int(prep["preparation_id"])
            key = (int(prep["category_id"]), int(prep["product_id"]))
            name = product_map.get(key, {}).get(
                "item_description", f'Product {prep["product_id"]}'
            )
            prepared_qty = int(prep["quantity_prepared"])
            sold_qty = sold_map.get(prep_id, 0)
            remaining_qty = prepared_qty - sold_qty      # Remaining = Prepared - Sold
            recorded = leftover_map.get(prep_id)

            if remaining_qty <= 0:
                level = "Sold out"
            elif remaining_qty <= max(1, round(prepared_qty * 0.2)):
                level = "Low"
            else:
                level = "In stock"

            stock_rows.append({
                "Category": category_names.get(key[0], "Unknown"),
                "Product": name,
                "Prepared": prepared_qty,
                "Sold": sold_qty,
                "Remaining": remaining_qty,
                "Stock Level": level,
                "Closed": "Yes" if recorded else "No",
                "Closing Count": int(recorded["quantity_remaining"]) if recorded else None,
                "Difference": (
                    int(recorded["quantity_remaining"]) - remaining_qty if recorded else None
                ),
            })

            if not recorded:
                pending_close.append((prep_id, max(remaining_qty, 0)))

        total_prepared = sum(r["Prepared"] for r in stock_rows)
        total_sold = sum(r["Sold"] for r in stock_rows)
        total_remaining = sum(r["Remaining"] for r in stock_rows)
        closed_count = len(stock_rows) - len(pending_close)

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Prepared", f"{total_prepared:,}")
        k2.metric("Sold", f"{total_sold:,}")
        k3.metric("Remaining Stock", f"{total_remaining:,}")
        k4.metric("Products Closed", f"{closed_count} of {len(stock_rows)}")

        st.subheader("Closing Stock per Product")
        st.caption(
            "Remaining = Prepared − Sold. Sold comes from every cashier sale, so this table "
            "changes the moment a sale is recorded or a prepared quantity is edited."
        )

        st.dataframe(
            stock_rows,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Prepared": st.column_config.NumberColumn(format="%d"),
                "Sold": st.column_config.NumberColumn(format="%d"),
                "Remaining": st.column_config.NumberColumn(format="%d"),
                "Closing Count": st.column_config.NumberColumn(format="%d"),
                "Difference": st.column_config.NumberColumn(format="%d"),
            },
        )
        st.caption(
            "Closing Count and Difference appear once a product is closed. "
            "Difference = Closing Count − Remaining."
        )

        if pending_close:
            st.info(
                f"{len(pending_close)} product(s) are not closed yet. Closing saves each "
                "remaining quantity above and stops the Sales Counter from selling them."
            )
            if st.button(
                "Close stock for the day",
                key="close_all_stock",
                type="primary",
                help="Saves Prepared − Sold as the closing stock for every open product.",
            ):
                try:
                    db.table("leftovers").insert([
                        {
                            "preparation_id": prep_id,
                            "quantity_remaining": qty,
                            "recorded_by": user["user_id"],
                            "notes": "Closed using Prepared - Sold",
                        }
                        for prep_id, qty in pending_close
                    ]).execute()
                    flash(f"Stock closed for {len(pending_close)} product(s).")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not close stock: {exc}")
        else:
            st.success("Stock is closed for every product on this date.")

        st.divider()

        with st.expander("Count and close one product at a time (optional)", expanded=False):
            st.caption(
                "Use this when the pieces you physically count are different from Prepared − Sold. "
                "Enter the actual quantity left for a product."
            )
            closing_rows = []

            for prep in prep_rows:
                prep_id = int(prep["preparation_id"])

                key = (
                    int(prep["category_id"]),
                    int(prep["product_id"]),
                )

                product = product_map.get(key, {})

                category = category_names.get(key[0], "Unknown")

                name = product.get(
                    "item_description",
                    f'Product {prep["product_id"]}',
                )

                prepared_qty = int(prep["quantity_prepared"])
                sold_qty = sold_map.get(prep_id, 0)

                expected_remaining = max(
                    prepared_qty - sold_qty,
                    0,
                )

                existing = leftover_map.get(prep_id)

                if existing:
                    actual_remaining = int(
                        existing["quantity_remaining"]
                    )

                    difference = (
                        actual_remaining - expected_remaining
                    )

                    if difference == 0:
                        status = "Balanced"
                    elif difference < 0:
                        status = "Short"
                    else:
                        status = "Extra"

                    closing_rows.append({
                        "Category": category,
                        "Product": name,
                        "Prepared": prepared_qty,
                        "Sold": sold_qty,
                        "Expected Remaining": expected_remaining,
                        "Actual Remaining": actual_remaining,
                        "Difference": difference,
                        "Status": status,
                    })

                    with st.container(border=True):
                        st.markdown(f"**{name}**")

                        st.caption(
                            f"Prepared: {prepared_qty} | "
                            f"Sold: {sold_qty} | "
                            f"Expected remaining: {expected_remaining}"
                        )

                        if status == "Balanced":
                            st.success(
                                f"Balanced: {actual_remaining} pieces counted."
                            )
                        elif status == "Short":
                            st.warning(
                                f"Short by {abs(difference)} pieces. "
                                f"Actual count: {actual_remaining}."
                            )
                        else:
                            st.warning(
                                f"{difference} more pieces than expected. "
                                f"Actual count: {actual_remaining}."
                            )

                        if existing.get("notes"):
                            st.caption(
                                f"Closing notes: {existing['notes']}"
                            )

                        if role == "owner" and st.button(
                            "↩ Undo closing",
                            key=f"undo_closing_{prep_id}",
                            help="Removes this closing count so you can enter it again.",
                        ):
                            ok, message = delete_row(
                                "leftovers", {"leftover_id": existing["leftover_id"]}, "the closing record"
                            )
                            if ok:
                                flash(f"{name}: closing undone. Enter the count again below.")
                                st.rerun()
                            else:
                                st.error(message)

                else:
                    # Include unclosed products in the summary.
                    closing_rows.append({
                        "Category": category,
                        "Product": name,
                        "Prepared": prepared_qty,
                        "Sold": sold_qty,
                        "Expected Remaining": expected_remaining,
                        "Actual Remaining": None,
                        "Difference": None,
                        "Status": "Not Closed",
                    })

                    with st.container(border=True):
                        st.markdown(f"**{name}**")

                        st.caption(
                            f"Prepared: {prepared_qty} | "
                            f"Sold: {sold_qty} | "
                            f"Expected remaining: {expected_remaining}"
                        )

                        st.info(
                            "Stock closing has not been recorded for this product."
                        )

                        with st.form(f"closing_form_{prep_id}"):

                            actual_remaining = st.number_input(
                                "Actual Remaining Stock (pieces)",
                                min_value=0,
                                max_value=prepared_qty,
                                value=None,
                                step=1,
                                key=f"actual_remaining_{prep_id}",
                            )

                            notes = st.text_input(
                                "Closing Notes (optional)",
                                placeholder=(
                                    "Example: Damaged, discarded, or missing items"
                                ),
                                key=f"closing_notes_{prep_id}",
                            )

                            save_closing = st.form_submit_button(
                                "Save Stock Closing",
                                type="primary",
                                use_container_width=True,
                            )

                        if save_closing:
                            if actual_remaining is None:
                                st.warning(
                                    "Please enter the actual counted quantity "
                                    "before saving."
                                )

                            else:
                                try:
                                    db.table("leftovers").insert({
                                        "preparation_id": prep_id,
                                        "quantity_remaining": int(
                                            actual_remaining
                                        ),
                                        "recorded_by": user["user_id"],
                                        "notes": notes.strip() or None,
                                    }).execute()

                                    st.success(
                                        f"{name}: stock closing recorded."
                                    )
                                    st.rerun()

                                except Exception as exc:
                                    st.error(
                                        f"Could not save stock closing: {exc}"
                                    )

        st.divider()
        st.button(
            f"Next: {PAGE_TITLES['Expenses']}  →",
            key="closing_next",
            type="primary",
            on_click=go_to_page,
            args=("Expenses",),
        )



# ------------------------- EXPENSES -------------------------

elif selected_page == "Expenses":
    page_header(
        "DAILY ROUTINE",
        "Expenses",
        "Log what was bought and review every expense. Totals update automatically.",
    )

    try:
        expense_categories = query_rows(
            "expense_categories",
            "expense_category_id, category_name, items_included, estimated_daily_cost",
        )

        expenses = query_rows(
            "expenses",
            "expense_id, expense_category_id, expense_date, amount, "
            "description, recorded_by, created_at",
        )

    except Exception as exc:
        st.error(f"Could not load expense records: {exc}")
        st.stop()

    category_names = {
        int(c["expense_category_id"]): c["category_name"]
        for c in expense_categories
    }

    def expense_day(e):
        return str(e.get("expense_date") or "")[:10]

    # ------------------------- RUNNING TOTALS (recalculated on every refresh) -------------------------

    today_date = date.today()
    month_start = today_date.replace(day=1)

    total_today = sum(as_float(e["amount"]) for e in expenses if expense_day(e) == today_date.isoformat())
    total_month = sum(
        as_float(e["amount"]) for e in expenses if in_range(expense_day(e), month_start, today_date)
    )
    total_all = sum(as_float(e["amount"]) for e in expenses)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Today", money(total_today))
    m2.metric("This Month", money(total_month))
    m3.metric("All Time", money(total_all))
    m4.metric("Entries Logged", f"{len(expenses):,}")

    # ------------------------- LOG AN EXPENSE -------------------------

    st.subheader("Log an Expense")

    if not expense_categories:
        st.warning(
            "No expense categories were found. "
            "Add them to expense_categories in Supabase first."
        )

    else:
        expense_options = {
            c["category_name"]: int(c["expense_category_id"])
            for c in expense_categories
        }

        with st.form("add_expense_form", clear_on_submit=True):
            item_name = st.text_input(
                "What was bought?",
                placeholder="Example: Pork belly, 5 kg",
            )

            f1, f2, f3 = st.columns([2, 1.2, 1.2])
            category_choice = f1.selectbox("Category", list(expense_options.keys()))
            amount = f2.number_input(
                "Cost (₱)",
                min_value=0.0,
                value=0.0,
                step=1.0,
                format="%.2f",
            )
            expense_date = f3.date_input("Date", value=date.today())

            notes = st.text_input(
                "Notes (optional)",
                placeholder="Supplier, receipt number, or other details",
            )

            save_expense = st.form_submit_button(
                "Add to Expense Log",
                type="primary",
                use_container_width=True,
            )

        if save_expense:
            if not item_name.strip():
                st.warning("Enter what was bought.")

            elif amount <= 0:
                st.warning("Enter a cost greater than zero.")

            else:
                description_text = item_name.strip()
                if notes.strip():
                    description_text += f" - {notes.strip()}"

                try:
                    db.table("expenses").insert({
                        "expense_category_id": expense_options[category_choice],
                        "expense_date": expense_date.isoformat(),
                        "amount": amount,
                        "description": description_text,
                        "recorded_by": user["user_id"],
                    }).execute()

                    flash(f"Logged {money(amount)} for {item_name.strip()}.")
                    st.rerun()

                except Exception as exc:
                    st.error(f"Could not save expense: {exc}")

        with st.expander("Category guide: what each category covers"):
            st.dataframe(
                [
                    {
                        "Category": c["category_name"],
                        "Items Included": c.get("items_included") or "",
                        "Estimated Daily Cost": as_float(c.get("estimated_daily_cost")),
                    }
                    for c in expense_categories
                ],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Estimated Daily Cost": st.column_config.NumberColumn(format="₱%.2f"),
                },
            )
            st.caption("Estimates are only a guide. Always log the actual amount spent.")

    st.divider()

    # ------------------------- EXPENSE LOG -------------------------

    st.subheader("Expense Log")

    l1, l2 = st.columns(2)
    with l1:
        log_view = st.selectbox(
            "Show",
            ["Today", "This Week", "This Month", "All Time", "Pick a date"],
            key="expense_log_view",
        )
        if log_view == "Pick a date":
            picked_day = st.date_input("Date", value=date.today(), key="expense_log_date")
            log_start = log_end = picked_day
        else:
            log_start, log_end = period_range(log_view)
    with l2:
        chosen_categories = st.multiselect(
            "Category",
            list(category_names.values()),
            key="expense_log_categories",
            placeholder="All categories",
        )

    visible = [
        e for e in expenses
        if in_range(expense_day(e), log_start, log_end)
        and (
            not chosen_categories
            or category_names.get(int(e["expense_category_id"]), "Unknown") in chosen_categories
        )
    ]
    visible.sort(
        key=lambda e: (e.get("expense_date") or "", e.get("created_at") or ""),
        reverse=True,
    )

    view_total = sum(as_float(e["amount"]) for e in visible)

    v1, v2 = st.columns(2)
    v1.metric("Total for this view", money(view_total))
    v2.metric("Entries", f"{len(visible):,}")

    if not visible:
        st.info("No expenses match this view.")

    else:
        def logged_at(e):
            stamp = parse_timestamp(e.get("created_at"))
            return stamp.strftime("%I:%M %p") if stamp else ""

        st.dataframe(
            [
                {
                    "Date": expense_day(e),
                    "Time Logged": logged_at(e),
                    "Item": e.get("description") or "",
                    "Category": category_names.get(int(e["expense_category_id"]), "Unknown"),
                    "Amount": as_float(e["amount"]),
                }
                for e in visible
            ],
            use_container_width=True,
            hide_index=True,
            column_config={
                "Amount": st.column_config.NumberColumn(format="₱%.2f"),
            },
        )
        st.caption(f"Total of the {len(visible):,} entries shown: {money(view_total)}.")

        by_category = {}
        for e in visible:
            label = category_names.get(int(e["expense_category_id"]), "Unknown")
            row = by_category.setdefault(label, {"entries": 0, "total": 0.0})
            row["entries"] += 1
            row["total"] += as_float(e["amount"])

        st.markdown("**Spending by category**")
        st.dataframe(
            [
                {
                    "Category": label,
                    "Entries": row["entries"],
                    "Total": row["total"],
                    "Share": (row["total"] / view_total * 100) if view_total else 0.0,
                }
                for label, row in sorted(by_category.items(), key=lambda kv: -kv[1]["total"])
            ],
            use_container_width=True,
            hide_index=True,
            column_config={
                "Total": st.column_config.NumberColumn(format="₱%.2f"),
                "Share": st.column_config.NumberColumn(format="%.1f%%"),
            },
        )

        if role == "owner":
            expense_delete_options = {
                f'{expense_day(e)} | {e.get("description") or "no description"} | '
                f'{money(e["amount"])} (#{e["expense_id"]})': e
                for e in visible[:100]
            }
            picked = delete_panel(
                "del_expense",
                "Delete an expense",
                expense_delete_options,
                "Use this for an expense entered by mistake. Lists up to 100 entries from the view above.",
            )
            if picked:
                ok, message = delete_row(
                    "expenses", {"expense_id": picked["expense_id"]}, "the expense"
                )
                if ok:
                    flash("Expense deleted.")
                    st.rerun()
                else:
                    st.error(message)


# ------------------------- USER MANAGEMENT -------------------------

elif selected_page == "User Management":
    page_header("MANAGE", "Users", "View SizzleLog profiles and link newly created Supabase Auth users.")
    if require_owner(user):
        try:
            profiles = query_rows(
                "app_user",
                "user_id, username, role, full_name, is_active, created_at",
            )
        except Exception as exc:
            st.error(f"Could not load user profiles: {exc}")
            st.stop()

        st.subheader("Registered Users")
        if profiles:
            st.dataframe(
                [
                    {
                        "Username": p["username"],
                        "Full Name": p.get("full_name") or "",
                        "Role": (p["role"] or "").capitalize(),
                        "Active": p["is_active"],
                        "User ID": p["user_id"],
                    }
                    for p in profiles
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No application profiles were found.")

        st.subheader("Link a Supabase Auth User")
        st.caption("First create the user's login under Supabase Dashboard → Authentication → Users. Then paste that user's UUID here. This page does not create or store passwords.")
        with st.form("add_profile_form"):
            auth_uid = st.text_input("Supabase Auth User UUID")
            new_username = st.text_input("Username")
            new_full_name = st.text_input("Full Name")
            new_role = st.selectbox("Role", ["cashier", "owner"])
            create_profile = st.form_submit_button("Add User Profile", type="primary")
        if create_profile:
            if not auth_uid.strip() or not new_username.strip():
                st.warning("User UUID and username are required.")
            else:
                try:
                    db.table("app_user").insert({
                        "user_id": auth_uid.strip(),
                        "username": new_username.strip(),
                        "full_name": new_full_name.strip() or None,
                        "role": new_role,
                        "is_active": True,
                    }).execute()
                    st.success("User profile linked successfully.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not add user profile: {exc}")

        if profiles:
            st.subheader("Change Account Status")
            profile_options = {
                f'{p["username"]} ({p["role"]})': p
                for p in profiles
                if str(p["user_id"]) != str(user["user_id"])
            }
            if profile_options:
                selected_profile_label = st.selectbox("Choose account", list(profile_options.keys()))
                selected_profile = profile_options[selected_profile_label]
                new_active_state = st.toggle(
                    "Account is active",
                    value=bool(selected_profile["is_active"]),
                    key=f'active_{selected_profile["user_id"]}',
                )
                if st.button("Save Account Status"):
                    try:
                        db.table("app_user").update({
                            "is_active": new_active_state
                        }).eq("user_id", selected_profile["user_id"]).execute()
                        st.success("Account status updated.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not update account status: {exc}")
            else:
                st.caption("No other accounts are available to manage.")

            if profile_options:
                picked = delete_panel(
                    "del_profile",
                    "Remove a user profile",
                    profile_options,
                    "This removes the SizzleLog profile only. Delete the person's login "
                    "separately in Supabase → Authentication → Users. Profiles that have "
                    "recorded sales, expenses or stock can't be removed: use "
                    "'Change Account Status' to deactivate them instead.",
                )
                if picked:
                    uid = picked["user_id"]
                    try:
                        activity = (
                            query_rows("sales", "sale_id", recorded_by=uid)
                            + query_rows("expenses", "expense_id", recorded_by=uid)
                            + query_rows("preparations", "preparation_id", recorded_by=uid)
                            + query_rows("leftovers", "leftover_id", recorded_by=uid)
                        )
                    except Exception as exc:
                        st.error(f"Could not check this profile before removing: {exc}")
                    else:
                        if activity:
                            st.error(
                                "This user has recorded business activity, so the profile "
                                "can't be removed. Deactivate the account instead."
                            )
                        else:
                            ok, message = delete_row("app_user", {"user_id": uid}, "the profile")
                            if ok:
                                flash("User profile removed.")
                                st.rerun()
                            else:
                                st.error(message)


# ------------------------- CASHIER TRANSACTION HISTORY -------------------------

elif selected_page == "My Transactions":
    page_header(
        "CASHIER",
        "My Transactions",
        "Review the customer sales you have recorded.",
    )

    try:
        my_sales = query_rows(
            "sales",
            "sale_id, sale_datetime, payment_method, notes",
            recorded_by=user["user_id"],
        )

        all_sale_items = query_rows(
            "sale_items",
            "sale_id, preparation_id, quantity_sold, unit_price",
        )

        all_preparations = query_rows(
            "preparations",
            "preparation_id, category_id, product_id",
        )

        products_map = get_product_map()

    except Exception as exc:
        st.error(f"Could not load your transactions: {exc}")
        st.stop()

    preparation_lookup = {
        int(p["preparation_id"]): (
            int(p["category_id"]),
            int(p["product_id"]),
        )
        for p in all_preparations
    }

    if not my_sales:
        st.info("You haven't recorded any sales yet.")
    else:
        my_sales = sorted(
            my_sales,
            key=lambda s: s.get("sale_datetime") or "",
            reverse=True,
        )

        transaction_rows = []

        for sale in my_sales:
            sale_id = int(sale["sale_id"])

            items = [
                item for item in all_sale_items
                if int(item["sale_id"]) == sale_id
            ]

            total = sum(
                int(item["quantity_sold"]) * as_float(item["unit_price"])
                for item in items
            )

            transaction_rows.append({
                "Receipt #": sale_id,
                "Date": str(sale["sale_datetime"])[:19].replace("T", " "),
                "Items Sold": sum(
                    int(item["quantity_sold"]) for item in items
                ),
                "Total": money(total),
                "Payment": sale.get("payment_method") or "Cash",
            })

        st.metric("Transactions Recorded", len(my_sales))

        st.subheader("Sales History")

        st.dataframe(
            transaction_rows,
            use_container_width=True,
            hide_index=True,
        )

        st.subheader("Transaction Details")

        for sale in my_sales:
            sale_id = int(sale["sale_id"])

            items = [
                item for item in all_sale_items
                if int(item["sale_id"]) == sale_id
            ]

            detail_rows = []
            total = 0

            for item in items:
                prep_key = preparation_lookup.get(
                    int(item["preparation_id"])
                )

                product = products_map.get(prep_key, {}) if prep_key else {}

                name = product.get(
                    "item_description",
                    "Product no longer available",
                )

                quantity = int(item["quantity_sold"])
                price = as_float(item["unit_price"])
                subtotal = quantity * price
                total += subtotal

                detail_rows.append({
                    "Product": name,
                    "Quantity": quantity,
                    "Unit Price": money(price),
                    "Subtotal": money(subtotal),
                })

            with st.expander(f"Receipt #{sale_id} | {money(total)}"):
                st.caption(
                    f'Date: {sale.get("sale_datetime", "Unknown date")}'
                )

                st.dataframe(
                    detail_rows,
                    use_container_width=True,
                    hide_index=True,
                )

                st.markdown(f"### Total: {money(total)}")
                st.caption(
                    f'Payment Method: {sale.get("payment_method") or "Cash"}'
                )



else:
    st.info("This page is not available for this account.")
