# Options Trading Performance Tracker

A Streamlit-based dashboard application that parses Fidelity "Accounts_History" CSV exports to track, analyze, and visualize options trading performance across multiple accounts and strategies. Also includes a live stock watchlist powered by Yahoo Finance.

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Installation](#installation)
3. [Starting the App](#starting-the-app)
4. [Stopping the App](#stopping-the-app)
5. [How to Use](#how-to-use)
6. [Detailed Design](#detailed-design)
7. [Python Libraries](#python-libraries)

---

## Quick Start

```bash
cd ~/options_tracker
pip3 install -r requirements.txt
streamlit run app.py
```

Open http://localhost:8501 in your browser. The **Watchlist** tab is available immediately; upload your Fidelity CSV via the sidebar to unlock the trading tabs.

---

## Installation

### Prerequisites

- Python 3.10 or higher
- pip (Python package manager)
- Internet access (required for the Watchlist tab to fetch live stock quotes)

### Install Dependencies

```bash
cd ~/options_tracker
pip3 install -r requirements.txt
```

This installs four packages (and their transitive dependencies):

| Package    | Purpose                                          |
|------------|--------------------------------------------------|
| streamlit  | Web application framework and UI                 |
| pandas     | Data parsing, cleaning, and aggregation          |
| plotly     | Interactive charts (bar, pie, scatter)            |
| yfinance   | Live stock quote data from Yahoo Finance         |

> **Note:** If your pip is configured to use a corporate proxy that doesn't mirror yfinance, install it directly from PyPI:
> ```bash
> pip3 install yfinance --index-url https://pypi.org/simple/
> ```

---

## Starting the App

### Standard Launch

```bash
cd ~/options_tracker
streamlit run app.py
```

The app will start on http://localhost:8501 by default.

### Custom Port

```bash
streamlit run app.py --server.port 8080
```

### Headless Mode (no browser auto-open)

```bash
streamlit run app.py --server.headless true
```

### Background Launch

```bash
cd ~/options_tracker
nohup streamlit run app.py --server.headless true > streamlit.log 2>&1 &
echo $! > streamlit.pid
```

---

## Stopping the App

### If Running in Foreground

Press `Ctrl+C` in the terminal where Streamlit is running.

### If Running in Background

```bash
# Using the saved PID file
kill $(cat ~/options_tracker/streamlit.pid)

# Or find and kill the process
pkill -f "streamlit run app.py"

# Verify it stopped
ps aux | grep streamlit
```

---

## How to Use

### Watchlist (no CSV required)

The **Watchlist** tab is always available, even before uploading any CSV file.

1. Open the app at http://localhost:8501
2. The Watchlist tab displays by default with pre-populated tickers (AAPL, AMZN, BMNR, META, MSFT, NVDA, ORCL, TSLA)
3. Edit the **Ticker Symbols** text field to add or remove symbols (comma-separated)
4. Select an **Auto-refresh** rate from the dropdown: Off, 1s, 5s, 10s, 15s, 30s, 60s, 2 min, or 5 min
5. Each ticker is displayed as a card showing:
   - Current price
   - Daily change ($ and %) with green/red color coding
   - Day high, day low, and volume
6. A detail table below the cards adds Market Cap, 52-week High, and 52-week Low

### Step 1: Export CSV from Fidelity

1. Log into Fidelity.com
2. Navigate to **Accounts** > **History**
3. Set the date range (e.g., Jan 1 to current date)
4. Click **Download** to export the CSV file

The CSV should have columns: `Run Date, Account, Account Number, Action, Symbol, Description, Type, Price ($), Quantity, Commission ($), Fees ($), Accrued Interest ($), Amount ($), Settlement Date`

### Step 2: Upload CSV

1. Open the app at http://localhost:8501
2. In the **left sidebar**, click the file upload widget
3. Select your Fidelity `Accounts_History` CSV file
4. The app automatically parses and displays results across all tabs

### Step 3: Apply Filters

The sidebar provides three multi-select filters:

- **Filter by Underlying** - Show only specific tickers (e.g., TSLA, NVDA, XSP)
- **Filter by Account** - Isolate by Fidelity account (BrokerageLink, Individual, Joint, ROTH IRA, HSA)
- **Filter by Strategy** - Focus on specific strategies (Cash Secured Put, Covered Call, Credit Spread)

All filters default to showing everything. Deselect items to narrow the view.

### Step 4: Explore the Dashboard

The dashboard is organized into five tabs:

**Tab 1 — Watchlist** (always available)
- Live stock price cards with auto-refresh
- Detail table with extended market data

**Tab 2 — Closed Trades & P&L** (primary analytics tab)
- KPI metrics row: Total Realized P&L, Closed Trades, Win Rate, Active Positions
- Sub-tabs for charts:
  - **P&L Over Time** — Bar chart (green/red) with cumulative line overlay. Toggle between Weekly and Monthly aggregation.
  - **Strategy Breakdown** — Donut chart of income by strategy + bar chart of total P&L per strategy with trade counts + summary table.
  - **Underlying Breakdown** — Bar chart of P&L by ticker symbol with a red-to-green color scale + summary table.
- Closed Trades Detail table: full lifecycle of every completed trade including open/close amounts and realized P&L, sorted by close date (most recent first).

**Tab 3 — Open Positions**
- Summary metrics: count, total open premium, positions expiring within 7 days
- Strategy breakdown table
- Full positions table sorted by expiration date, then underlying

**Tab 4 — Dividends & Other Income**
- Summary metrics: Total Dividends, Total Interest, Other Amounts
- Category summary table (Dividend, Interest, Reinvestment, Transfer, Fee, Stock Trade, Other)
- Sub-tabs for each category with individual transaction details

**Tab 5 — Raw Transactions**
- Raw option transactions table with all parsed fields
- Raw non-option transactions table with category labels

---

## Detailed Design

### Architecture Overview

The application follows a three-stage pipeline plus a standalone watchlist module:

```
CSV File  -->  [1. Parse & Clean]  -->  [2. Trade Engine]  -->  [3. Dashboard UI]
                 load_and_parse()        build_trades()         Streamlit widgets
                 (returns option_df      (groups into trades,
                  + non_option_df)        computes P&L)

Yahoo Finance  -->  [Watchlist Module]  -->  [Watchlist Tab]
                      fetch_quotes()          st.fragment auto-refresh
                      yfinance fast_info
```

All logic resides in a single `app.py` file (~823 lines) with core functions and the Streamlit UI layer.

---

### Stage 1: Data Parsing (`load_and_parse`)

This function handles the messy reality of Fidelity's CSV export format and returns two DataFrames: `option_df` (option transactions) and `non_option_df` (dividends, interest, stock trades, etc.).

#### CSV Structure Handling

Fidelity CSVs have:
- 2 blank lines before the header row
- A disclaimer/legal footer after the data rows
- Quoted fields with embedded commas in the Action column

The parser:
1. Scans for the header row by looking for a line containing both `"Run Date"` and `"Action"`
2. Collects only data lines that start with a date pattern `MM/DD/YYYY`
3. Skips blank lines and disclaimer text (lines starting with "The data", "Brokerage", etc.)
4. Feeds the cleaned lines into `pandas.read_csv()` via `io.StringIO`

#### Option Symbol Parsing (`parse_option_symbol`)

Fidelity encodes option contracts in a compact symbol format:

```
-BMNR260306C27
 │    │     ││
 │    │     │└── Strike price: $27
 │    │     └─── Option type: C=Call, P=Put
 │    └───────── Expiration: 2026-03-06 (YYMMDD)
 └────────────── Underlying ticker: BMNR
```

The regex pattern used:

```python
r'^-([A-Z]+)(\d{6})([CP])([\d.]+)$'
```

| Group | Captures         | Example  | Notes                                  |
|-------|------------------|----------|----------------------------------------|
| 1     | `[A-Z]+`         | `BMNR`   | 1-5 letter ticker symbol               |
| 2     | `\d{6}`          | `260306` | Date in YYMMDD format                  |
| 3     | `[CP]`           | `C`      | Call or Put indicator                   |
| 4     | `[\d.]+`         | `27`     | Strike price, supports decimals (11.5)  |

The date string is parsed with `datetime.strptime(date_str, '%y%m%d')` to produce a proper `date` object.

#### Action Classification (`classify_action`)

Fidelity's Action column contains verbose, variable-length strings like:

```
"YOU SOLD OPENING TRANSACTION PUT (NVDL) GRANITESHARES ETF TR MAR 06 26 $67 (100 SHS) (Margin)"
"EXPIRED CALL (BMNR) BITMINE IMMERSION MAR 06 26 $27 as of Mar-06-2026 ..."
"ASSIGNED as of Feb-06-2026 PUT (BMNR) BITMINE IMMERSION FEB 06 26 $21 (100 SHS) (Cash)"
```

The classifier normalizes these into six action types using substring matching:

| Normalized Type   | Matches on               | Meaning                         |
|-------------------|--------------------------|----------------------------------|
| `SOLD_OPENING`    | "SOLD OPENING"           | Opened a short position          |
| `BOUGHT_OPENING`  | "BOUGHT OPENING"         | Opened a long position           |
| `BOUGHT_CLOSING`  | "BOUGHT CLOSING"         | Closed a short position by buying back |
| `SOLD_CLOSING`    | "SOLD CLOSING"           | Closed a long position by selling |
| `EXPIRED`         | "EXPIRED"                | Option expired worthless ($0)    |
| `ASSIGNED`        | "ASSIGNED"               | Option was exercised/assigned    |

Priority order matters: EXPIRED is checked before BOUGHT/SOLD to avoid false matches on the verbose expired action strings that also contain transaction-like text.

#### Non-Option Classification (`classify_non_option`)

Transactions that don't match an option symbol pattern are classified into categories by scanning the Action and Description fields:

| Category      | Matches on                           |
|---------------|--------------------------------------|
| Dividend      | "DIVIDEND" in Action or Description  |
| Interest      | "INTEREST" in Action or Description  |
| Reinvestment  | "REINVEST" in Action or Description  |
| Transfer      | "TRANSFER" in Action or Description  |
| Fee           | "FEE" in Action or Description       |
| Stock Trade   | "BOUGHT" or "SOLD" in Action         |
| Other         | Everything else                      |

#### Numeric Cleaning

- `Price ($)`, `Amount ($)`, `Commission ($)`, `Fees ($)` are converted to float via `pd.to_numeric()` with comma stripping
- `Quantity` is converted to integer
- NaN values in numeric columns are filled with 0.0
- `Run Date` is parsed as datetime with `MM/DD/YYYY` format

---

### Stage 2: Trade Engine (`build_trades`)

This is the core analytical engine that groups raw option transactions into logical trades and computes P&L. Only option transactions are processed here.

#### Two-Pass Processing

The engine runs in two passes:

**Pass 1 - Spread Detection:**

1. Filter to only opening transactions (SOLD_OPENING, BOUGHT_OPENING)
2. Create a composite group key: `Account | Underlying | Expiration | Run Date`
3. For each group, check if it contains **both** SOLD_OPENING and BOUGHT_OPENING legs
4. If yes, classify as a credit spread and process as a single multi-leg trade
5. Track which transaction indices were consumed by spread processing

The spread type is determined by the option types of the legs:

| Sold Leg Type | Bought Leg Type | Strategy            |
|---------------|-----------------|---------------------|
| Call          | Call            | Call Credit Spread  |
| Put           | Put             | Put Credit Spread   |
| Mixed         | Mixed           | Credit Spread       |

Net credit = sum of all leg amounts (sold legs are positive, bought legs are negative in Fidelity's format).

Strike description is built as: `"Sold 664.0/665.0/666.0 / Bought 660.0/661.0/662.0"` to show all strikes in multi-contract spreads.

**Pass 2 - Single Leg Trades:**

1. Take all opening transactions NOT consumed by Pass 1
2. Group by Account + Symbol (aggregates multiple fills of the same contract)
3. Classify strategy based on action type and option type:

| Action Type    | Option Type | Strategy          |
|----------------|-------------|-------------------|
| SOLD_OPENING   | Call        | Covered Call      |
| SOLD_OPENING   | Put         | Cash Secured Put  |
| BOUGHT_OPENING | Call        | Long Call         |
| BOUGHT_OPENING | Put         | Long Put          |

#### Trade Lifecycle Matching

For each trade (spread or single leg), the engine searches for closing events:

1. Filter the full dataset for rows matching: same Account, same Symbol(s), and action type in {EXPIRED, ASSIGNED, BOUGHT_CLOSING, SOLD_CLOSING}
2. If closing transactions are found:
   - Sum their `Amount ($)` as the close amount
   - Use the latest `Run Date` as the close date
   - Compute `realized_pnl = open_amount + close_amount`
   - Mark status as "Closed"
3. If no closing transactions found, the trade remains "Open"

#### P&L Calculation

Fidelity's `Amount ($)` column already accounts for:
- Contract multiplier (100 shares per contract)
- Commissions and fees (subtracted from credits, added to debits)

For **sold options** (credit): Amount is positive (e.g., +$94.77)
For **bought options** (debit): Amount is negative (e.g., -$42.23)
For **expired** options: Amount is $0.00

Therefore:
```
Realized P&L = Open Amount + Close Amount
```

Example - Cash Secured Put:
- Open: SOLD OPENING PUT, Amount = +$94.77 (credit received)
- Close: EXPIRED, Amount = $0.00
- P&L = $94.77 + $0.00 = **+$94.77** (profit - kept the premium)

Example - Credit Spread:
- Open: SOLD PUT $666 = +$75.77, BOUGHT PUT $662 = -$42.23, Net = +$33.54
- Close: Both EXPIRED = $0.00
- P&L = $33.54 + $0.00 = **+$33.54**

Example - Bought Closing (rolled or managed):
- Open: SOLD PUT $59 = +$443.33
- Close: BOUGHT CLOSING PUT $59 = -$419.67
- P&L = $443.33 + (-$419.67) = **+$23.66**

---

### Watchlist Module

The Watchlist is independent of the CSV upload pipeline and is always available.

#### Data Fetching (`fetch_quotes`)

For each ticker symbol, the module calls `yfinance.Ticker(symbol).fast_info` to retrieve:

| Field              | Source Attribute     | Display                    |
|--------------------|----------------------|----------------------------|
| Current Price      | `last_price`         | Card + table               |
| Previous Close     | `previous_close`     | Used to compute change     |
| Day High           | `day_high`           | Card + table               |
| Day Low            | `day_low`            | Card + table               |
| Volume             | `last_volume`        | Card + table               |
| Market Cap         | `market_cap`         | Table only (formatted as B/M) |
| 52-Week High       | `year_high`          | Table only                 |
| 52-Week Low        | `year_low`           | Table only                 |

Daily change is computed as: `change = price - previous_close`, `change_pct = change / previous_close * 100`.

#### Auto-Refresh

The watchlist uses `st.fragment(run_every=N)` to refresh only the quotes display section without re-running the entire app. This approach:
- Does not block user interaction with other tabs
- Only refreshes the watchlist portion of the page
- Configurable intervals: Off, 1s, 5s, 10s, 15s, 30s, 60s, 2 min, 5 min

#### Display

Quotes are rendered as styled HTML cards (3 per row) with:
- Ticker symbol in bold
- Large price display
- Green ▲ / Red ▼ change indicator
- Day high, low, and volume summary line

Below the cards, a full `st.dataframe` table shows all fields including Market Cap and 52-week range.

---

### Stage 3: Dashboard UI

#### Layout Structure

```
+--sidebar--+  +--main area-------------------------------------------------+
| Upload CSV |  | [KPI: P&L] [KPI: Trades] [KPI: Win%] [KPI: Open]          |
| Filter:    |  |------------------------------------------------------------ |
|  Underlying|  | Tab: Watchlist | Closed P&L | Open | Dividends | Raw       |
|  Account   |  |                                                            |
|  Strategy  |  | (tab content area)                                         |
+------------+  +------------------------------------------------------------+
```

When no CSV is uploaded, only the Watchlist tab is shown. After uploading, all five tabs appear.

#### KPI Metrics

Four `st.metric()` widgets in a 4-column layout (shown above tabs when data is loaded):
- **Total Realized P&L**: `closed['realized_pnl'].sum()`
- **Closed Trades**: `len(closed)`
- **Win Rate**: count of trades where `realized_pnl > 0` divided by total closed trades
- **Active Positions**: `len(active)`

#### Closed Trades & P&L Tab

Contains three chart sub-tabs:

- **P&L Over Time**: User selects Weekly or Monthly aggregation. A Plotly Figure combines a bar trace (green/red per-period P&L) with a line+marker trace (cumulative P&L). Uses `hovermode='x unified'` for synchronized tooltips.
- **Strategy Breakdown**: Side-by-side donut chart (profitable strategies) and bar chart (all strategies with trade counts), plus a summary table.
- **Underlying Breakdown**: Bar chart with continuous red-to-green color scale based on P&L, plus a summary table.

Below the charts: Closed Trades Detail table with full lifecycle data.

#### Open Positions Tab

- Summary metrics: count, total open premium, expiring within 7 days
- Strategy breakdown table
- Full positions table sorted by expiration then underlying

#### Dividends & Other Income Tab

- Summary metrics: Total Dividends, Total Interest, Other Amounts
- Category summary table
- Sub-tabs for each category (Dividend, Interest, Reinvestment, etc.) with transaction details

#### Raw Transactions Tab

- Raw option transactions table with all parsed fields
- Raw non-option transactions table with category labels

All dollar amounts throughout are formatted as `$X,XXX.XX` using f-strings with `,.2f` formatting.

---

### Data Flow Diagram

```
Fidelity CSV Upload
        |
        v
  load_and_parse()
   |-- Find header row (skip blank lines)
   |-- Filter data rows (date pattern match)
   |-- pd.read_csv() into DataFrame
   |-- Clean: dates, numerics, strip whitespace
   |-- parse_option_symbol() on each Symbol
   |-- classify_action() on each Action
   |-- Split into option_df + non_option_df
   |-- classify_non_option() on non_option_df
        |
        +--------- option_df --------+--------- non_option_df -------+
        |                            |                               |
        v                            v                               v
  build_trades()              Closed Trades &                  Dividends &
   |-- Pass 1: Spreads        Open Positions tabs              Other Income tab
   |-- Pass 2: Single legs
   |-- Match closings
   |-- Compute P&L
        |
        v
  DataFrame: 1 row per trade
  Columns: trade_id, account, underlying, strategy, strikes,
           quantity, open_date, close_date, open_amount,
           close_amount, realized_pnl, status
        |
        v
  Streamlit Dashboard
   |-- Sidebar filters (underlying, account, strategy)
   |-- KPI metrics
   |-- Tabbed Plotly charts (closed trades only)
   |-- Open positions table
   |-- Closed trades detail table
   |-- Dividends & income tables
   |-- Raw transactions tables


Yahoo Finance (yfinance)
        |
        v
  fetch_quotes()
   |-- yf.Ticker(symbol).fast_info per ticker
   |-- Compute daily change & change %
        |
        v
  Watchlist Tab (st.fragment auto-refresh)
   |-- Styled HTML price cards (3 per row)
   |-- Detail table (Price, Change, Volume, Market Cap, 52W range)
```

---

## Python Libraries

### Streamlit (v1.55+)

**Role:** Web application framework

Streamlit converts Python scripts into interactive web applications without requiring HTML, CSS, or JavaScript. The script is re-executed top-to-bottom on every user interaction (widget change, file upload, filter selection).

**Key components used:**

| Component                | Purpose                                                    |
|--------------------------|------------------------------------------------------------|
| `st.set_page_config()`  | Set page title, wide layout mode                           |
| `st.sidebar`            | Left panel for upload widget and filters                   |
| `st.file_uploader()`    | File upload widget, restricted to .csv                     |
| `st.multiselect()`      | Multi-option dropdown filters                              |
| `st.columns()`          | Horizontal column layout for KPIs and side-by-side charts  |
| `st.metric()`           | Large KPI display widgets with labels                      |
| `st.tabs()`             | Tabbed interface for main sections and chart sub-sections  |
| `st.radio()`            | Weekly/Monthly toggle for time aggregation                 |
| `st.text_input()`       | Ticker symbol entry for watchlist                          |
| `st.selectbox()`        | Auto-refresh rate picker for watchlist                     |
| `st.fragment()`         | Partial re-run for watchlist auto-refresh without blocking |
| `st.plotly_chart()`     | Render Plotly figures in the app                           |
| `st.dataframe()`        | Render pandas DataFrames as interactive tables             |
| `st.markdown()`         | Horizontal rules, text formatting, and HTML cards          |
| `st.caption()`          | Small text for "Last updated" timestamp                    |
| `st.session_state`      | Persist watchlist tickers across reruns                    |
| `st.info()` / `st.error()` | Status messages and error feedback                     |

**Execution model:** Streamlit runs `app.py` as a server. Each browser session gets its own state. When a widget value changes (e.g., a filter), the entire script re-executes with updated widget values. `st.fragment` is an exception — it allows partial re-execution of a decorated function without re-running the full script, which is used for watchlist auto-refresh.

### Pandas (v2.3+)

**Role:** Data manipulation and analysis

Pandas provides the DataFrame abstraction for all data operations in the application.

**Key operations used:**

| Operation                            | Purpose                                              |
|--------------------------------------|------------------------------------------------------|
| `pd.read_csv()`                      | Parse the cleaned CSV text into a DataFrame          |
| `pd.to_datetime()`                   | Convert date strings to datetime objects             |
| `pd.to_numeric()`                    | Convert currency strings to float                    |
| `Series.apply()`                     | Apply symbol parser and action classifier per row    |
| `DataFrame.groupby()`               | Group transactions for spread detection, aggregation, and category summaries |
| `.agg()`                             | Multi-column aggregation (sum, count, lambda)        |
| `Series.dt.to_period('W'/'M')`      | Bucket dates into weekly/monthly periods             |
| `.cumsum()`                          | Running cumulative sum for cumulative P&L line       |
| `DataFrame.sort_values()`           | Sort tables by date, underlying, P&L                 |
| Boolean indexing                     | Filter rows by action type, account, symbol, category |
| `.isin()`                            | Multi-value filter for set membership                |
| `.fillna()`                          | Replace NaN with 0.0 for numeric columns             |
| `.dropna()`                          | Remove rows with missing dates                       |
| `.rename(columns={})`               | Rename columns for display-friendly labels           |

**DataFrame lifecycle:**
1. Raw CSV -> all rows x 14 columns (all transactions)
2. Split into option_df (with parsed fields) + non_option_df (with categories)
3. Option trades aggregated -> 1 row per trade (with P&L)
4. Filtered by user selections -> subset displayed

### Plotly (v6.6+)

**Role:** Interactive charting

Plotly produces charts that support hover tooltips, zoom, pan, and export-to-PNG directly in the browser. Two Plotly modules are used:

**`plotly.graph_objects` (go)** - Low-level figure construction:

| Component        | Purpose                                              |
|------------------|------------------------------------------------------|
| `go.Figure()`    | Create a composite figure with multiple traces       |
| `go.Bar()`       | Bar chart trace with per-bar conditional coloring    |
| `go.Scatter()`   | Line+marker trace for cumulative P&L overlay         |
| `fig.update_layout()` | Set title, axis labels, hover mode              |

The P&L Over Time chart uses `go` because it needs two different trace types (bar + line) on the same axes with per-bar color control (green for profit, red for loss).

**`plotly.express` (px)** - High-level chart functions:

| Function      | Purpose                                              |
|---------------|------------------------------------------------------|
| `px.pie()`    | Donut chart for strategy income distribution         |
| `px.bar()`    | Bar charts for strategy P&L and underlying P&L       |

Express functions are used for simpler charts where a single trace with automatic color mapping suffices.

**Chart customizations:**
- `hole=0.3` on pie chart creates a donut style
- `color_continuous_scale=['red', 'green']` on underlying bar chart maps negative P&L to red, positive to green
- `texttemplate='%{text} trades'` adds trade count annotations above bars
- `hovermode='x unified'` synchronizes tooltips across traces at the same x-position

### yfinance (v1.2+)

**Role:** Live stock quote data

yfinance wraps Yahoo Finance's publicly available APIs to fetch real-time and historical stock market data. The app uses the `fast_info` attribute for lightweight, quick quote lookups.

**Key usage:**

| API                          | Data Retrieved                                    |
|------------------------------|---------------------------------------------------|
| `yf.Ticker(symbol)`         | Create a ticker object for a given symbol         |
| `.fast_info.last_price`     | Most recent traded price                          |
| `.fast_info.previous_close` | Previous trading day's closing price              |
| `.fast_info.day_high`       | Current session's high price                      |
| `.fast_info.day_low`        | Current session's low price                       |
| `.fast_info.last_volume`    | Most recent trading volume                        |
| `.fast_info.market_cap`     | Company market capitalization                     |
| `.fast_info.year_high`      | 52-week high price                                |
| `.fast_info.year_low`       | 52-week low price                                 |

Each ticker is fetched independently with error handling so that a single failed lookup doesn't break the entire watchlist.

> **Note:** yfinance is intended for research and educational purposes. Yahoo Finance may rate-limit or block excessive requests.

### Python Standard Library

| Module     | Purpose                                    |
|------------|--------------------------------------------|
| `re`       | Regex for option symbol parsing and date pattern matching |
| `datetime` | Date parsing from YYMMDD format and watchlist timestamps |
| `io`       | `StringIO` to feed cleaned CSV text to pandas |

---

## File Structure

```
~/options_tracker/
  app.py              # Main application (~823 lines)
  requirements.txt    # Python dependencies (streamlit, pandas, plotly, yfinance)
  README.md           # This file
```

---

## Supported Fidelity Account Types

The app has been validated with these Fidelity account types:

- BrokerageLink (401k brokerage window)
- Individual - TOD (individual taxable)
- Joint WROS - TOD (joint taxable)
- ROTH IRA
- Health Savings Account (HSA)

Each account is tracked independently - trades are never grouped across accounts.

---

## Limitations

- **No stock position tracking**: Covered Call classification assumes any short call is covered. The app does not verify that the user holds 100 shares of the underlying.
- **Same-day spread detection only**: Legs must be opened on the same Run Date to be grouped as a spread. Legs opened on different days are treated as separate single-leg trades.
- **No rolling detection**: If a position is closed and a new one opened on the same day for the same contract, both are tracked but not linked as a "roll."
- **Assignment handling**: Assigned options are treated as $0 close events (same as expiry). Stock assignment or cash settlement effects are not tracked.
- **Single CSV at a time**: The app processes one uploaded file per session. To analyze a longer history, export a wider date range from Fidelity.
- **Watchlist data source**: yfinance wraps Yahoo Finance's public APIs which may be rate-limited or temporarily unavailable. Quotes may be delayed up to 15 minutes for some exchanges.
- **Non-option classification**: The `classify_non_option` function uses keyword matching on Action and Description fields. Unusual Fidelity action strings may be categorized as "Other."
