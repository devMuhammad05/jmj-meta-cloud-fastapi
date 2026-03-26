# Metrics Storage Documentation

This document describes how trading metrics are stored and managed in the JMJ Meta Cloud FastAPI application.

## Overview

The system uses **PostgreSQL** to store trading performance metrics. Metrics tracking begins from the moment an account is created in our database, ignoring any historical data from MetaAPI.

## Storage Strategy

### Core Principles

1. **Fresh Start**: Metrics tracking begins from the `created_at` timestamp in our database, not from historical API data
2. **Minimal Initial Storage**: Only the `balance` is fetched and stored from the API on account creation
3. **Database Defaults**: All other metric fields default to `0.0` in the database
4. **Incremental Tracking**: Metrics accumulate only from activities that occur after account registration

### Why This Approach?

- **Clean Baseline**: Each account starts with a known state (initial balance, zero trades)
- **Accurate Attribution**: Only tracks performance from when we started monitoring
- **Simplified Storage**: No need to import or reconcile historical data from external sources
- **Consistent Metrics**: All accounts measured from the same starting point (registration date)

## Database Tables

### `meta_account_metrics`

Stores trading performance metrics for each MetaAPI account.

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `id` | Primary Key | - | Auto-generated identifier |
| `account_id` | UUID | - | Reference to MetaAPI account |
| `balance` | Numeric | - | Current account balance (fetched from API) |
| `equity` | Numeric | `0.0` | Current equity |
| `profit` | Numeric | `0.0` | Total profit/loss since registration |
| `deposits` | Numeric | `0.0` | Deposits since registration |
| `withdrawals` | Numeric | `0.0` | Withdrawals since registration |
| `margin` | Numeric | `0.0` | Used margin |
| `free_margin` | Numeric | `0.0` | Available margin |
| `trades` | Integer | `0` | Number of trades since registration |
| `profit_factor` | Numeric | `0.0` | Profitability factor |
| `sharpe_ratio` | Numeric | `0.0` | Risk-adjusted return |
| `won_trades_percent` | Numeric | `0.0` | Percentage of winning trades |
| `lost_trades_percent` | Numeric | `0.0` | Percentage of losing trades |
| `daily_growth` | JSON Array | `[]` | Daily balance history (from registration date) |
| `created_at` | Timestamp | `NOW()` | **Metrics tracking start date** |
| `updated_at` | Timestamp | `NOW()` | Last update timestamp |

### `meta_trader_credentials`

Stores MT5 account credentials and configuration.

| Column | Type | Description |
|--------|------|-------------|
| `id` | Primary Key | Auto-generated identifier |
| `user_id` | String | User identifier |
| `mt_account_number` | String | MT5 login number |
| `mt_password` | String | MT5 account password |
| `mt_server` | String | MT5 server name |
| `platform_type` | String | Trading platform (e.g., 'mt5') |
| `initial_deposit` | Numeric | Starting balance at registration |
| `risk_level` | String | Risk level setting |
| `account_id` | UUID | MetaAPI account UUID |
| `created_at` | Timestamp | Registration timestamp |
| `updated_at` | Timestamp | Update timestamp |

## Data Flow

### Account Provisioning (Initial Setup)

```
┌─────────────────┐     ┌──────────────┐     ┌─────────────────┐
│  Provision      │────▶│  FastAPI     │────▶│  MetaAPI        │
│  Request        │     │  Endpoint    │     │  (get balance)  │
└─────────────────┘     └──────────────┘     └─────────────────┘
                               │                      │
                               ▼                      │
                        ┌──────────────┐              │
                        │  PostgreSQL  │◀─────────────┘
                        │  - balance   │   (only balance stored)
                        │  - created_at│   (other fields = 0.0)
                        └──────────────┘
```

**On account creation:**
1. Fetch current `balance` from MetaAPI
2. Insert record with `balance` and `created_at = NOW()`
3. All other metrics default to `0.0` (database defaults)

### Metrics Updates (Ongoing)

```
┌─────────────────┐     ┌──────────────┐     ┌─────────────────┐
│  Stats Request  │────▶│  FastAPI     │────▶│  MetaAPI        │
└─────────────────┘     │  Endpoint    │     │  (fetch data)   │
                        └──────────────┘     └─────────────────┘
                               │                      │
                               ▼                      │
                        ┌──────────────┐              │
                        │  Filter by   │◀─────────────┘
                        │  created_at  │
                        └──────────────┘
                               │
                               ▼
                        ┌──────────────┐
                        │  PostgreSQL  │
                        │  (update)    │
                        └──────────────┘
```

**On metrics update:**
1. Fetch trades/metrics from MetaAPI
2. **Filter**: Only include data where `trade_date >= created_at`
3. Calculate metrics based on filtered data only
4. Update database record

## Metrics Calculation Logic

### Filtering Historical Data

When fetching from MetaAPI, all data is filtered by the account's `created_at` timestamp:

```python
# Pseudocode
account_created_at = get_account_created_at(account_id)

# Only count trades after registration
relevant_trades = [
    trade for trade in api_trades
    if trade.close_time >= account_created_at
]

# Calculate metrics from filtered trades only
metrics = calculate_metrics(relevant_trades)
```

### What Gets Stored

| Field | Source | Notes |
|-------|--------|-------|
| `balance` | API | Always current balance from API |
| `profit` | Calculated | Sum of P/L from trades after `created_at` |
| `trades` | Calculated | Count of trades closed after `created_at` |
| `daily_growth` | Calculated | Balance snapshots starting from `created_at` |
| Other fields | Calculated | Derived from post-registration activity only |

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/provision-account` | POST | Create account, store initial balance |
| `/api/trading-stats` | POST | Fetch and update metrics (filtered by created_at) |

## Key Implementation Notes

1. **Database defaults handle initialization** - No need to explicitly set `0.0` values on insert
2. **`created_at` is the source of truth** - All metric calculations filter by this date
3. **Balance is always current** - Unlike other metrics, balance reflects real-time API value
4. **Daily growth starts fresh** - Array begins from registration date, not account opening date

## File Locations

| Purpose | File |
|---------|------|
| Database setup | `app/database.py` |
| Metrics logic | `app/routers/trading_stats.py` |
| Account provisioning | `app/routers/provison_account.py` |
| Request schemas | `app/models/stats.py`, `app/models/provision.py` |
