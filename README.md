# SizzleLog — BBQ Business Management System 

**SizzleLog** is a Streamlit-based point-of-sale (POS) and daily operations management web app designed for a barbecue business, backed by **Supabase** for authentication and database storage. It supports role-based access for **Owners** and **Cashiers** with a custom wine/coral frosted-glass dashboard theme.

---

## Features

### Authentication & Role-Based Access
* **Supabase Auth Login**: Sign in with email and password against a frosted-glass login screen.
* **Profile & Active Status Check**: Verifies the signed-in user exists in `app_user` and has `is_active` enabled.
* **Owner vs. Cashier Navigation**:
  * **Owner**: Accesses **Overview**, **Prepare Stock**, **Sales** (Sales Breakdown + Sales Counter toggle), **Close Stock**, **Expenses**, **Products**, and **Users**.
  * **Cashier**: Accesses **Sales Counter** and **My Transactions**.

### Daily Routine & Operations
* **Overview Dashboard**:
  * Displays daily business status (e.g., *Preparation Not Started*, *Pre-Opening* before 6:00 PM, *Business Hours*, or *Stock Closed for the Day*) and a guided **Next step** button.
  * **Live Business Summary**: Auto-refreshes every 5 seconds (`@st.fragment(run_every=5)`) with period filters (**Today**, **This Week**, **This Month**, **All Time**) showing Total Sales Amount, Total Expenses, Net Earnings (`Sales − Expenses`), Transactions, Pieces Sold, and Expense Entries.
  * **Today's Routine Cards**: Quick status and navigation for *1 · Prepare Stock*, *2 · Sales Counter*, *3 · Close Stock*, and *Record an Expense*.
  * **Previous Business Day**: Collapsible summary of yesterday's sales, expenses, transactions, and pieces sold.
* **Prepare Stock (`Preparation`)**:
  * Set prepared quantities per product for a selected business date with instant `on_change` saving to Supabase.
  * Prevents lowering prepared quantities below units already sold, allows clearing un-sold entries (`0`), and locks closed products until reopened.
* **Sales & Sales Counter (`Sales`)**:
  * **Sales Counter (POS)**: Filter available stock by category, add pieces to the current order, adjust quantities or remove items, calculate cash change, record `sales` and `sale_items` with real-time stock re-validation, and display a formatted receipt.
  * **Sales Breakdown (Owner)**: Filter by date or view all dates, inspect units sold per product with a bar chart, and browse item-by-item receipt lines.
* **Close Stock (`Leftovers`)**:
  * Calculates remaining stock (`Prepared − Sold`) per product and lets the owner close all open items in one click or record/undo individual physical counts with variance (`Balanced`, `Short`, `Extra`) and notes.
* **Expenses (`Expenses`)**:
  * Shows running totals for Today, This Month, All Time, and entry count.
  * Log new expenses with category, cost (`₱`), date, and optional notes; view a category reference guide; filter the expense log; inspect spending share by category; and delete mistaken entries (owner).
* **Products & User Management (Owner)**:
  * **Products**: Add, search, filter, edit, or safely delete (only if never prepared) products and reference leftover figures.
  * **Users**: View `app_user` profiles, link existing Supabase Auth user UUIDs with a role (`cashier` or `owner`), toggle account active status, or remove unused profiles.
* **My Transactions (Cashier)**:
  * View personal recorded sales history and expand individual receipts for item-level totals.

---

## Project Structure

```text
.
├── app.py                  # Main Streamlit application, Supabase queries, POS, and workflows
├── assets/
│   ├── style.css           # Wine/coral frosted-glass theme, hero, sidebar, POS cards, login UI
│   ├── logo.png            # Primary brand logo (optional fallback: assets/logo.ico)
│   ├── hero_bbq.jpg        # Hero banner background image (optional)
│   └── login-bg.*          # Optional login background (.jpg, .jpeg, .png, .webp)
└── .env                    # Environment variables (not committed)
