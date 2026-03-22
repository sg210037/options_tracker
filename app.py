import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import re
from datetime import datetime, timedelta
import io
import yfinance as yf

# --- Page Config ---
st.set_page_config(page_title="Options Trading Tracker", layout="wide")
st.title("Options Trading Performance Tracker")

# --- Option Symbol Parser ---
def parse_option_symbol(symbol):
    """Parse Fidelity option symbol like -BMNR260306C27 or -TSLL260306P11.5"""
    if pd.isna(symbol):
        return None
    symbol = symbol.strip()
    m = re.match(r'^-([A-Z]+)(\d{6})([CP])([\d.]+)$', symbol)
    if not m:
        return None
    underlying = m.group(1)
    date_str = m.group(2)
    option_type = 'Call' if m.group(3) == 'C' else 'Put'
    strike = float(m.group(4))
    try:
        expiration = datetime.strptime(date_str, '%y%m%d').date()
    except ValueError:
        return None
    return {
        'underlying': underlying,
        'expiration': expiration,
        'option_type': option_type,
        'strike': strike,
    }


def classify_action(action_str):
    """Classify the action into a normalized type."""
    if pd.isna(action_str):
        return None, None
    a = action_str.upper()
    if 'EXPIRED' in a:
        cp = 'CALL' if 'CALL' in a else 'PUT' if 'PUT' in a else None
        return 'EXPIRED', cp
    if 'ASSIGNED' in a:
        cp = 'CALL' if 'CALL' in a else 'PUT' if 'PUT' in a else None
        return 'ASSIGNED', cp
    if 'BOUGHT CLOSING' in a:
        cp = 'CALL' if 'CALL' in a else 'PUT' if 'PUT' in a else None
        return 'BOUGHT_CLOSING', cp
    if 'SOLD CLOSING' in a:
        cp = 'CALL' if 'CALL' in a else 'PUT' if 'PUT' in a else None
        return 'SOLD_CLOSING', cp
    if 'BOUGHT OPENING' in a:
        cp = 'CALL' if 'CALL' in a else 'PUT' if 'PUT' in a else None
        return 'BOUGHT_OPENING', cp
    if 'SOLD OPENING' in a:
        cp = 'CALL' if 'CALL' in a else 'PUT' if 'PUT' in a else None
        return 'SOLD_OPENING', cp
    return None, None


def classify_non_option(action_str, description_str):
    """Classify non-option transactions into categories."""
    if pd.isna(action_str):
        return 'Other'
    a = action_str.upper()
    d = str(description_str).upper() if not pd.isna(description_str) else ''

    if 'DIVIDEND' in a or 'DIVIDEND' in d:
        return 'Dividend'
    if 'INTEREST' in a or 'INTEREST' in d:
        return 'Interest'
    if 'REINVEST' in a or 'REINVEST' in d:
        return 'Reinvestment'
    if 'TRANSFER' in a or 'TRANSFER' in d:
        return 'Transfer'
    if 'FEE' in a or 'FEE' in d:
        return 'Fee'
    if any(kw in a for kw in ['BOUGHT', 'SOLD', 'YOU BOUGHT', 'YOU SOLD']):
        return 'Stock Trade'
    return 'Other'


def load_and_parse(uploaded_file):
    """Load CSV, skip header rows, parse all data. Returns (option_df, other_df)."""
    content = uploaded_file.read().decode('utf-8')
    lines = content.splitlines()

    header_idx = None
    for i, line in enumerate(lines):
        if 'Run Date' in line and 'Action' in line:
            header_idx = i
            break
    if header_idx is None:
        st.error("Could not find header row with 'Run Date' and 'Action'")
        return None, None

    data_lines = [lines[header_idx]]
    for line in lines[header_idx + 1:]:
        stripped = line.strip().strip('"')
        if not stripped or stripped.startswith('The data') or stripped.startswith('Brokerage') or stripped.startswith('Date downloaded') or stripped.startswith('Fidelity'):
            continue
        if re.match(r'^\d{2}/\d{2}/\d{4}', stripped):
            data_lines.append(line)

    csv_text = '\n'.join(data_lines)
    df = pd.read_csv(io.StringIO(csv_text))
    df.columns = df.columns.str.strip()

    if 'Account' not in df.columns:
        account_col = [c for c in df.columns if 'account' in c.lower()]
        if account_col:
            df.rename(columns={account_col[0]: 'Account'}, inplace=True)
        else:
            df['Account'] = 'Default'

    df['Run Date'] = pd.to_datetime(df['Run Date'], format='%m/%d/%Y', errors='coerce')

    for col in ['Price ($)', 'Amount ($)', 'Commission ($)', 'Fees ($)']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0.0)

    df['Quantity'] = pd.to_numeric(df['Quantity'], errors='coerce').fillna(0).astype(int)

    parsed = df['Symbol'].apply(parse_option_symbol)
    df['is_option'] = parsed.apply(lambda x: x is not None)
    df['underlying'] = parsed.apply(lambda x: x['underlying'] if x else None)
    df['expiration'] = parsed.apply(lambda x: x['expiration'] if x else None)
    df['option_type'] = parsed.apply(lambda x: x['option_type'] if x else None)
    df['strike'] = parsed.apply(lambda x: x['strike'] if x else None)

    classified = df['Action'].apply(classify_action)
    df['action_type'] = classified.apply(lambda x: x[0])
    df['action_cp'] = classified.apply(lambda x: x[1])

    # Split into option and non-option transactions
    option_df = df[df['is_option']].copy()
    option_df = option_df.dropna(subset=['Run Date'])

    non_option_df = df[~df['is_option']].copy()
    non_option_df = non_option_df.dropna(subset=['Run Date'])
    desc_col = 'Description' if 'Description' in non_option_df.columns else None
    non_option_df['category'] = non_option_df.apply(
        lambda row: classify_non_option(row['Action'], row.get('Description') if desc_col else ''),
        axis=1
    )

    return option_df, non_option_df


def build_trades(df):
    """
    Group legs into trades and compute P&L.

    Strategy: For each account + symbol (contract), aggregate all opening amounts
    and all closing amounts. The P&L is the sum of all amounts (credits are positive,
    debits are negative in Fidelity's format).

    For strategy classification:
    - Iron Condor: same group has both Call and Put among sold AND bought legs
      (i.e., a Call Credit Spread + Put Credit Spread on the same underlying/expiration/date)
    - Credit/Debit Spread: same underlying, same expiration, same run date, same account,
      has both SOLD OPENING and BOUGHT OPENING legs (calls only or puts only).
      Net positive amount = credit spread, net negative = debit spread.
    - Covered Call: SOLD OPENING CALL (single leg)
    - Cash Secured Put: SOLD OPENING PUT (single leg)
    """
    trades = []

    opening_txns = df[df['action_type'].isin(['SOLD_OPENING', 'BOUGHT_OPENING'])].copy()

    opening_txns['group_key'] = (
        opening_txns['Account'].astype(str) + '|' +
        opening_txns['underlying'].astype(str) + '|' +
        opening_txns['expiration'].astype(str) + '|' +
        opening_txns['Run Date'].dt.date.astype(str)
    )

    spread_groups = set()
    for gk, grp in opening_txns.groupby('group_key'):
        actions_in_group = set(grp['action_type'].unique())
        if 'SOLD_OPENING' in actions_in_group and 'BOUGHT_OPENING' in actions_in_group:
            spread_groups.add(gk)

    spread_indices = set()

    # --- Process Spreads ---
    for gk in spread_groups:
        grp = opening_txns[opening_txns['group_key'] == gk]
        spread_indices.update(grp.index.tolist())

        account = grp['Account'].iloc[0]
        underlying = grp['underlying'].iloc[0]
        expiration = grp['expiration'].iloc[0]
        run_date = grp['Run Date'].iloc[0]

        sold_legs = grp[grp['action_type'] == 'SOLD_OPENING']
        bought_legs = grp[grp['action_type'] == 'BOUGHT_OPENING']

        net_credit = sold_legs['Amount ($)'].sum() + bought_legs['Amount ($)'].sum()
        total_qty = sold_legs['Quantity'].abs().sum()

        sold_types = set(sold_legs['option_type'].unique())
        bought_types = set(bought_legs['option_type'].unique())

        has_call_spread = 'Call' in sold_types and 'Call' in bought_types
        has_put_spread = 'Put' in sold_types and 'Put' in bought_types

        if has_call_spread and has_put_spread:
            strategy = 'Iron Condor'

            sold_calls = sold_legs[sold_legs['option_type'] == 'Call']
            bought_calls = bought_legs[bought_legs['option_type'] == 'Call']
            sold_puts = sold_legs[sold_legs['option_type'] == 'Put']
            bought_puts = bought_legs[bought_legs['option_type'] == 'Put']

            put_strikes = sorted(
                list(bought_puts['strike'].unique()) + list(sold_puts['strike'].unique())
            )
            call_strikes = sorted(
                list(sold_calls['strike'].unique()) + list(bought_calls['strike'].unique())
            )
            strike_desc = (
                f"P {'/'.join(str(s) for s in put_strikes)} | "
                f"C {'/'.join(str(s) for s in call_strikes)}"
            )
        elif has_call_spread:
            spread_type = 'Credit' if net_credit >= 0 else 'Debit'
            strategy = f'Call {spread_type} Spread'
            sold_strikes = sorted(sold_legs['strike'].unique())
            bought_strikes = sorted(bought_legs['strike'].unique())
            strike_desc = f"Sold {'/'.join(str(s) for s in sold_strikes)} / Bought {'/'.join(str(s) for s in bought_strikes)}"
        elif has_put_spread:
            spread_type = 'Credit' if net_credit >= 0 else 'Debit'
            strategy = f'Put {spread_type} Spread'
            sold_strikes = sorted(sold_legs['strike'].unique())
            bought_strikes = sorted(bought_legs['strike'].unique())
            strike_desc = f"Sold {'/'.join(str(s) for s in sold_strikes)} / Bought {'/'.join(str(s) for s in bought_strikes)}"
        else:
            spread_type = 'Credit' if net_credit >= 0 else 'Debit'
            strategy = f'{spread_type} Spread'
            sold_strikes = sorted(sold_legs['strike'].unique())
            bought_strikes = sorted(bought_legs['strike'].unique())
            strike_desc = f"Sold {'/'.join(str(s) for s in sold_strikes)} / Bought {'/'.join(str(s) for s in bought_strikes)}"

        spread_symbols = set(grp['Symbol'].str.strip().unique())

        trade = {
            'trade_id': len(trades),
            'account': account,
            'underlying': underlying,
            'expiration': expiration,
            'strategy': strategy,
            'option_type': '/'.join(sorted(sold_types | bought_types)),
            'strikes': strike_desc,
            'quantity': total_qty,
            'open_date': run_date,
            'close_date': None,
            'open_amount': net_credit,
            'close_amount': 0.0,
            'realized_pnl': None,
            'status': 'Open',
            'symbols': spread_symbols,
        }

        closing_mask = (
            df['action_type'].isin(['EXPIRED', 'ASSIGNED', 'BOUGHT_CLOSING', 'SOLD_CLOSING']) &
            (df['Account'] == account) &
            (df['underlying'] == underlying) &
            (df['expiration'] == expiration) &
            df['Symbol'].str.strip().isin(spread_symbols)
        )
        closing_txns = df[closing_mask]

        if len(closing_txns) > 0:
            close_amount = closing_txns['Amount ($)'].sum()
            trade['close_date'] = closing_txns['Run Date'].max()
            trade['close_amount'] = close_amount
            trade['realized_pnl'] = net_credit + close_amount
            trade['status'] = 'Closed'

        trades.append(trade)

    # --- Process Single Legs (non-spread) ---
    single_openings = opening_txns[~opening_txns.index.isin(spread_indices)]

    for (account, symbol), grp in single_openings.groupby(['Account', 'Symbol']):
        symbol_clean = symbol.strip()
        parsed = parse_option_symbol(symbol_clean)
        if not parsed:
            continue

        action_type = grp['action_type'].iloc[0]
        opt_type = parsed['option_type']

        if action_type == 'SOLD_OPENING':
            if opt_type == 'Call':
                strategy = 'Covered Call'
            else:
                strategy = 'Cash Secured Put'
        else:
            strategy = f"Long {opt_type}"

        net_amount = grp['Amount ($)'].sum()
        total_qty = grp['Quantity'].abs().sum()
        open_date = grp['Run Date'].min()

        trade = {
            'trade_id': len(trades),
            'account': account,
            'underlying': parsed['underlying'],
            'expiration': parsed['expiration'],
            'strategy': strategy,
            'option_type': opt_type,
            'strikes': str(parsed['strike']),
            'quantity': total_qty,
            'open_date': open_date,
            'close_date': None,
            'open_amount': net_amount,
            'close_amount': 0.0,
            'realized_pnl': None,
            'status': 'Open',
            'symbols': {symbol_clean},
        }

        closing_mask = (
            df['action_type'].isin(['EXPIRED', 'ASSIGNED', 'BOUGHT_CLOSING', 'SOLD_CLOSING']) &
            (df['Account'] == account) &
            (df['Symbol'].str.strip() == symbol_clean)
        )
        closing_txns = df[closing_mask]

        if len(closing_txns) > 0:
            close_amount = closing_txns['Amount ($)'].sum()
            trade['close_date'] = closing_txns['Run Date'].max()
            trade['close_amount'] = close_amount
            trade['realized_pnl'] = net_amount + close_amount
            trade['status'] = 'Closed'

        trades.append(trade)

    return pd.DataFrame(trades)


def fetch_quotes(tickers):
    """Fetch current quote data for a list of ticker symbols."""
    results = []
    for symbol in tickers:
        try:
            t = yf.Ticker(symbol)
            fi = t.fast_info
            price = fi.last_price
            prev_close = fi.previous_close
            if price and prev_close and prev_close != 0:
                change = price - prev_close
                change_pct = (change / prev_close) * 100
            else:
                change = 0.0
                change_pct = 0.0
            results.append({
                'Symbol': symbol.upper(),
                'Price': price or 0.0,
                'Change': change,
                'Change %': change_pct,
                'Day High': fi.day_high or 0.0,
                'Day Low': fi.day_low or 0.0,
                'Volume': fi.last_volume or 0,
                'Market Cap': getattr(fi, 'market_cap', None),
                '52W High': getattr(fi, 'year_high', None),
                '52W Low': getattr(fi, 'year_low', None),
            })
        except Exception:
            results.append({
                'Symbol': symbol.upper(),
                'Price': None, 'Change': None, 'Change %': None,
                'Day High': None, 'Day Low': None, 'Volume': None,
                'Market Cap': None, '52W High': None, '52W Low': None,
            })
    return results


def render_watchlist_quotes(tickers):
    """Display quote cards and detail table for the given tickers."""
    quotes = fetch_quotes(tickers)

    last_updated = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    st.caption(f"Last updated: {last_updated}")

    for row_start in range(0, len(quotes), 3):
        row_quotes = quotes[row_start:row_start + 3]
        cols = st.columns(3)
        for col, q in zip(cols, row_quotes):
            with col:
                if q['Price'] is None:
                    st.error(f"**{q['Symbol']}** — Failed to fetch")
                    continue

                change_pct = q['Change %']
                arrow = "▲" if change_pct >= 0 else "▼"
                color = "green" if change_pct >= 0 else "red"
                sign = "+" if change_pct >= 0 else ""

                w52_high = f"${q['52W High']:,.2f}" if pd.notna(q['52W High']) else "—"
                w52_low = f"${q['52W Low']:,.2f}" if pd.notna(q['52W Low']) else "—"

                st.markdown(
                    f"""
                    <div style="border:1px solid #333; border-radius:10px; padding:16px 18px; margin-bottom:8px; background:#0e1117;">
                        <div style="font-size:1.3em; font-weight:700;">{q['Symbol']}</div>
                        <div style="font-size:1.8em; font-weight:700; margin:4px 0;">
                            ${q['Price']:,.2f}
                        </div>
                        <div style="color:{color}; font-size:1.05em;">
                            {arrow} {sign}{q['Change']:,.2f} ({sign}{change_pct:.2f}%)
                        </div>
                        <div style="font-size:0.8em; color:#888; margin-top:8px;">
                            H: ${q['Day High']:,.2f} &nbsp;|&nbsp; L: ${q['Day Low']:,.2f} &nbsp;|&nbsp; Vol: {q['Volume']:,.0f}
                        </div>
                        <div style="font-size:0.8em; color:#888; margin-top:4px;">
                            52W H: {w52_high} &nbsp;|&nbsp; 52W L: {w52_low}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

    st.markdown("---")
    detail_df = pd.DataFrame(quotes)
    valid = detail_df.dropna(subset=['Price'])
    if len(valid) > 0:
        display = valid.copy()
        display['Price'] = display['Price'].apply(lambda x: f"${x:,.2f}")
        display['Change'] = display['Change'].apply(lambda x: f"{'+' if x >= 0 else ''}{x:,.2f}")
        display['Change %'] = display['Change %'].apply(lambda x: f"{'+' if x >= 0 else ''}{x:.2f}%")
        display['Day High'] = display['Day High'].apply(lambda x: f"${x:,.2f}" if pd.notna(x) else "—")
        display['Day Low'] = display['Day Low'].apply(lambda x: f"${x:,.2f}" if pd.notna(x) else "—")
        display['Volume'] = display['Volume'].apply(lambda x: f"{x:,.0f}" if pd.notna(x) else "—")
        display['Market Cap'] = display['Market Cap'].apply(
            lambda x: f"${x/1e9:,.1f}B" if pd.notna(x) and x > 1e9
            else f"${x/1e6:,.0f}M" if pd.notna(x) and x > 0
            else "—"
        )
        display['52W High'] = display['52W High'].apply(lambda x: f"${x:,.2f}" if pd.notna(x) else "—")
        display['52W Low'] = display['52W Low'].apply(lambda x: f"${x:,.2f}" if pd.notna(x) else "—")
        st.dataframe(display, hide_index=True, width="stretch")


REFRESH_OPTIONS = {
    "Off": 0,
    "Every 1s": 1,
    "Every 5s": 5,
    "Every 10s": 10,
    "Every 15s": 15,
    "Every 30s": 30,
    "Every 60s": 60,
    "Every 2 min": 120,
    "Every 5 min": 300,
}


def render_watchlist_tab():
    """Render the Watchlist tab with ticker input and auto-refreshing prices."""
    DEFAULT_TICKERS = "AAPL, AMZN, BMNR, GOOG, META, MSFT, NVDA, ORCL, TSLA"

    st.subheader("Watchlist")

    wl_col1, wl_col2 = st.columns([3, 1])
    with wl_col1:
        tickers_input = st.text_input(
            "Ticker Symbols (comma-separated)",
            value=st.session_state.get('watchlist_tickers', DEFAULT_TICKERS),
            key='watchlist_input',
            placeholder="e.g. AAPL, MSFT, TSLA, NVDA"
        )
    with wl_col2:
        refresh_label = st.selectbox(
            "Auto-refresh",
            options=list(REFRESH_OPTIONS.keys()),
            index=5,
            key='watchlist_refresh'
        )

    st.session_state['watchlist_tickers'] = tickers_input
    refresh_seconds = REFRESH_OPTIONS[refresh_label]

    tickers = [t.strip().upper() for t in tickers_input.split(',') if t.strip()]
    if not tickers:
        st.info("Enter one or more ticker symbols above to track prices.")
        return

    # Use st.fragment with run_every for non-blocking auto-refresh
    run_every = refresh_seconds if refresh_seconds > 0 else None

    @st.fragment(run_every=run_every)
    def _watchlist_fragment():
        render_watchlist_quotes(tickers)

    _watchlist_fragment()


# --- Sidebar ---
st.sidebar.header("Upload & Filters")
uploaded_file = st.sidebar.file_uploader("Upload Fidelity Accounts_History CSV", type=['csv'])

# --- Determine which tabs to show ---
has_data = False
option_df = None
non_option_df = None
trades_df = None
closed = pd.DataFrame()
active = pd.DataFrame()

if uploaded_file is not None:
    option_df, non_option_df = load_and_parse(uploaded_file)
    if option_df is not None and len(option_df) > 0:
        has_data = True
        trades_df = build_trades(option_df)

        # --- Filters ---
        underlyings = sorted(trades_df['underlying'].dropna().unique())
        selected_underlying = st.sidebar.multiselect(
            "Filter by Underlying", underlyings, default=underlyings
        )

        accounts = sorted(trades_df['account'].dropna().unique())
        selected_accounts = st.sidebar.multiselect(
            "Filter by Account", accounts, default=accounts
        )

        strategies = sorted(trades_df['strategy'].dropna().unique())
        selected_strategies = st.sidebar.multiselect(
            "Filter by Strategy", strategies, default=strategies
        )

        mask = (
            trades_df['underlying'].isin(selected_underlying) &
            trades_df['account'].isin(selected_accounts) &
            trades_df['strategy'].isin(selected_strategies)
        )
        filtered = trades_df[mask].copy()

        closed = filtered[filtered['status'] == 'Closed'].copy()
        active = filtered[filtered['status'] == 'Open'].copy()

# --- KPI Row (shown when data loaded) ---
if has_data:
    st.markdown("---")
    col1, col2, col3, col4 = st.columns(4)

    total_pnl = closed['realized_pnl'].sum() if len(closed) > 0 else 0
    total_trades = len(closed)
    wins = len(closed[closed['realized_pnl'] > 0]) if len(closed) > 0 else 0
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
    active_count = len(active)

    col1.metric("Total Realized P&L", f"${total_pnl:,.2f}")
    col2.metric("Closed Trades", f"{total_trades}")
    col3.metric("Win Rate", f"{win_rate:.1f}%")
    col4.metric("Active Positions", f"{active_count}")

# --- Top-Level Tabs (Watchlist always present) ---
st.markdown("---")

if has_data:
    tab_names = [
        "Watchlist",
        "Closed Trades & P&L",
        "Open Positions",
        "Dividends & Other Income",
        "Raw Transactions",
    ]
else:
    tab_names = ["Watchlist"]

main_tabs = st.tabs(tab_names)
tab_idx = 0

# =============================================
# TAB: Watchlist (always available)
# =============================================
with main_tabs[tab_idx]:
    render_watchlist_tab()
tab_idx += 1

if has_data:
    # =============================================
    # TAB: Closed Trades & P&L (primary)
    # =============================================
    with main_tabs[tab_idx]:
        if len(closed) > 0:
            chart_tabs = st.tabs(["P&L Over Time", "Strategy Breakdown", "Underlying Breakdown"])

            with chart_tabs[0]:
                time_agg = st.radio("Aggregate by", ["Weekly", "Monthly"], horizontal=True, key='time_agg')

                closed_with_date = closed.dropna(subset=['close_date']).copy()
                if len(closed_with_date) > 0:
                    if time_agg == "Weekly":
                        closed_with_date['period'] = closed_with_date['close_date'].dt.to_period('W').apply(
                            lambda r: r.start_time
                        )
                    else:
                        closed_with_date['period'] = closed_with_date['close_date'].dt.to_period('M').apply(
                            lambda r: r.start_time
                        )

                    pnl_by_period = closed_with_date.groupby('period')['realized_pnl'].sum().reset_index()
                    pnl_by_period.columns = ['Period', 'P&L']
                    pnl_by_period['Cumulative P&L'] = pnl_by_period['P&L'].cumsum()
                    pnl_by_period['Color'] = pnl_by_period['P&L'].apply(lambda x: 'green' if x >= 0 else 'red')

                    fig = go.Figure()
                    fig.add_trace(go.Bar(
                        x=pnl_by_period['Period'],
                        y=pnl_by_period['P&L'],
                        marker_color=pnl_by_period['Color'],
                        name='Period P&L'
                    ))
                    fig.add_trace(go.Scatter(
                        x=pnl_by_period['Period'],
                        y=pnl_by_period['Cumulative P&L'],
                        mode='lines+markers',
                        name='Cumulative P&L',
                        line=dict(color='blue', width=2)
                    ))
                    fig.update_layout(
                        title=f"{time_agg} Realized P&L",
                        xaxis_title="Period",
                        yaxis_title="P&L ($)",
                        hovermode='x unified'
                    )
                    st.plotly_chart(fig, width="stretch")

            with chart_tabs[1]:
                strategy_pnl = closed.groupby('strategy').agg(
                    total_pnl=('realized_pnl', 'sum'),
                    count=('trade_id', 'count'),
                    wins=('realized_pnl', lambda x: (x > 0).sum())
                ).reset_index()
                strategy_pnl['win_rate'] = (strategy_pnl['wins'] / strategy_pnl['count'] * 100).round(1)

                all_strategies = sorted(strategy_pnl['strategy'].unique())
                palette = px.colors.qualitative.Plotly
                strategy_colors = {s: palette[i % len(palette)] for i, s in enumerate(all_strategies)}

                col_a, col_b = st.columns(2)
                with col_a:
                    pie_data = strategy_pnl[strategy_pnl['total_pnl'] > 0]
                    fig_pie = px.pie(
                        pie_data,
                        values='total_pnl',
                        names='strategy',
                        title='Income by Strategy (Profitable Only)',
                        hole=0.3,
                        color='strategy',
                        color_discrete_map=strategy_colors
                    )
                    st.plotly_chart(fig_pie, width="stretch")

                with col_b:
                    fig_bar = px.bar(
                        strategy_pnl,
                        x='strategy',
                        y='total_pnl',
                        color='strategy',
                        title='Total P&L by Strategy',
                        text='count',
                        color_discrete_map=strategy_colors
                    )
                    fig_bar.update_traces(texttemplate='%{text} trades', textposition='outside')
                    st.plotly_chart(fig_bar, width="stretch")

                st.dataframe(
                    strategy_pnl.rename(columns={
                        'strategy': 'Strategy',
                        'total_pnl': 'Total P&L ($)',
                        'count': 'Trades',
                        'wins': 'Wins',
                        'win_rate': 'Win Rate (%)'
                    }),
                    hide_index=True,
                    width="stretch"
                )

            with chart_tabs[2]:
                underlying_pnl = closed.groupby('underlying').agg(
                    total_pnl=('realized_pnl', 'sum'),
                    count=('trade_id', 'count'),
                    wins=('realized_pnl', lambda x: (x > 0).sum())
                ).reset_index().sort_values('total_pnl', ascending=False)
                underlying_pnl['win_rate'] = (underlying_pnl['wins'] / underlying_pnl['count'] * 100).round(1)

                fig_und = px.bar(
                    underlying_pnl,
                    x='underlying',
                    y='total_pnl',
                    color='total_pnl',
                    color_continuous_scale=['red', 'green'],
                    title='P&L by Underlying',
                    text='count'
                )
                fig_und.update_traces(texttemplate='%{text} trades', textposition='outside')
                st.plotly_chart(fig_und, width="stretch")

                st.dataframe(
                    underlying_pnl.rename(columns={
                        'underlying': 'Underlying',
                        'total_pnl': 'Total P&L ($)',
                        'count': 'Trades',
                        'wins': 'Wins',
                        'win_rate': 'Win Rate (%)'
                    }),
                    hide_index=True,
                    width="stretch"
                )

            st.markdown("---")
            st.subheader("Closed Trades Detail")
            closed_display = closed[[
                'account', 'underlying', 'strategy', 'option_type',
                'strikes', 'expiration', 'quantity', 'open_date', 'close_date',
                'open_amount', 'close_amount', 'realized_pnl'
            ]].copy()
            closed_display.columns = [
                'Account', 'Underlying', 'Strategy', 'Type',
                'Strikes', 'Expiration', 'Qty', 'Open Date', 'Close Date',
                'Open Amount ($)', 'Close Amount ($)', 'Realized P&L ($)'
            ]
            closed_display = closed_display.sort_values('Close Date', ascending=False)
            for col in ['Open Amount ($)', 'Close Amount ($)', 'Realized P&L ($)']:
                closed_display[col] = closed_display[col].apply(lambda x: f"${x:,.2f}")
            st.dataframe(closed_display, hide_index=True, width="stretch")
        else:
            st.info("No closed trades to display.")
    tab_idx += 1

    # =============================================
    # TAB: Open Positions
    # =============================================
    with main_tabs[tab_idx]:
        if len(active) > 0:
            st.subheader(f"Active Positions ({len(active)})")

            oc1, oc2, oc3 = st.columns(3)
            total_open_premium = active['open_amount'].sum()
            expiring_soon = active[
                active['expiration'].apply(
                    lambda x: (pd.Timestamp(x) - pd.Timestamp.now()).days <= 7 if pd.notna(x) else False
                )
            ]
            oc1.metric("Open Positions", f"{len(active)}")
            oc2.metric("Total Open Premium", f"${total_open_premium:,.2f}")
            oc3.metric("Expiring Within 7 Days", f"{len(expiring_soon)}")

            st.markdown("---")

            st.markdown("##### By Strategy")
            strategy_counts = active.groupby('strategy').agg(
                count=('trade_id', 'count'),
                total_premium=('open_amount', 'sum')
            ).reset_index()
            strategy_counts.columns = ['Strategy', 'Count', 'Total Premium ($)']
            strategy_counts['Total Premium ($)'] = strategy_counts['Total Premium ($)'].apply(lambda x: f"${x:,.2f}")
            st.dataframe(strategy_counts, hide_index=True, width="stretch")

            st.markdown("---")

            st.markdown("##### All Open Positions")
            active_display = active[[
                'account', 'underlying', 'strategy', 'option_type',
                'strikes', 'expiration', 'quantity', 'open_date', 'open_amount'
            ]].copy()
            active_display.columns = [
                'Account', 'Underlying', 'Strategy', 'Type',
                'Strikes', 'Expiration', 'Qty', 'Open Date', 'Open Amount ($)'
            ]
            active_display = active_display.sort_values(['Expiration', 'Underlying'])
            active_display['Open Amount ($)'] = active_display['Open Amount ($)'].apply(lambda x: f"${x:,.2f}")
            st.dataframe(active_display, hide_index=True, width="stretch")
        else:
            st.info("No active positions.")
    tab_idx += 1

    # =============================================
    # TAB: Dividends & Other Income
    # =============================================
    with main_tabs[tab_idx]:
        if non_option_df is not None and len(non_option_df) > 0:
            categories = sorted(non_option_df['category'].unique())

            dc1, dc2, dc3 = st.columns(3)
            total_dividends = non_option_df.loc[
                non_option_df['category'] == 'Dividend', 'Amount ($)'
            ].sum()
            total_interest = non_option_df.loc[
                non_option_df['category'] == 'Interest', 'Amount ($)'
            ].sum()
            total_other = non_option_df.loc[
                ~non_option_df['category'].isin(['Dividend', 'Interest']), 'Amount ($)'
            ].sum()
            dc1.metric("Total Dividends", f"${total_dividends:,.2f}")
            dc2.metric("Total Interest", f"${total_interest:,.2f}")
            dc3.metric("Other Amounts", f"${total_other:,.2f}")

            st.markdown("---")

            cat_summary = non_option_df.groupby('category').agg(
                count=('Amount ($)', 'count'),
                total=('Amount ($)', 'sum')
            ).reset_index().sort_values('total', ascending=False)
            cat_summary.columns = ['Category', 'Transactions', 'Total Amount ($)']
            cat_summary['Total Amount ($)'] = cat_summary['Total Amount ($)'].apply(lambda x: f"${x:,.2f}")
            st.dataframe(cat_summary, hide_index=True, width="stretch")

            st.markdown("---")

            if len(categories) > 0:
                cat_tabs = st.tabs(categories)
                for cat_tab, cat_name in zip(cat_tabs, categories):
                    with cat_tab:
                        cat_df = non_option_df[non_option_df['category'] == cat_name].copy()
                        display_cols = ['Run Date', 'Account', 'Action', 'Symbol']
                        if 'Description' in cat_df.columns:
                            display_cols.append('Description')
                        display_cols.extend(['Amount ($)'])
                        cat_display = cat_df[display_cols].copy()
                        cat_display = cat_display.sort_values('Run Date', ascending=False)
                        cat_display['Amount ($)'] = cat_display['Amount ($)'].apply(lambda x: f"${x:,.2f}")
                        st.dataframe(cat_display, hide_index=True, width="stretch")
        else:
            st.info("No dividends, interest, or other non-option transactions found.")
    tab_idx += 1

    # =============================================
    # TAB: Raw Transactions
    # =============================================
    with main_tabs[tab_idx]:
        st.subheader("Raw Option Transactions")
        raw_display = option_df[[
            'Run Date', 'Account', 'Action', 'Symbol', 'underlying',
            'option_type', 'strike', 'expiration', 'action_type',
            'Price ($)', 'Quantity', 'Amount ($)'
        ]].copy()
        st.dataframe(raw_display, hide_index=True, width="stretch")

        if non_option_df is not None and len(non_option_df) > 0:
            st.markdown("---")
            st.subheader("Raw Non-Option Transactions")
            non_opt_cols = ['Run Date', 'Account', 'Action', 'Symbol']
            if 'Description' in non_option_df.columns:
                non_opt_cols.append('Description')
            non_opt_cols.extend(['Amount ($)', 'category'])
            raw_other = non_option_df[non_opt_cols].copy()
            raw_other = raw_other.rename(columns={'category': 'Category'})
            st.dataframe(raw_other, hide_index=True, width="stretch")

elif not has_data and uploaded_file is not None:
    st.warning("No option transactions found in the uploaded file.")

if not has_data and uploaded_file is None:
    st.markdown("""
    ### Getting Started
    Upload a Fidelity Accounts_History CSV file from the sidebar to see your options trading data, or use the **Watchlist** tab above to track stock prices.

    ### Expected CSV Format
    The CSV should contain columns: **Run Date, Action, Symbol, Price ($), Quantity, Amount ($)**
    Optional columns: **Account, Description, Type, Commission ($), Fees ($), Settlement Date**

    ### Features
    - **Watchlist**: Track live stock prices with configurable auto-refresh
    - **Option Symbol Parsing**: Extracts underlying, expiration, strike, and type from symbols like `-BMNR260306C27`
    - **Strategy Detection**: Identifies Credit Spreads, Covered Calls, and Cash Secured Puts
    - **Trade Lifecycle**: Links opening and closing/expired transactions to compute realized P&L
    - **Interactive Dashboard**: Weekly/Monthly P&L charts, strategy breakdown, win rate, and active positions
    - **Dividends & Income**: Tracks dividends, interest, and other non-option transactions separately
    """)
