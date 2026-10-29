# Mini SMS Gateway

A robust, SMS Gateway API built with Django and MSSQL Server. This system handles user authentication, wallet management, priority-based provider routing, failover logic, and strict concurrency control to prevent wallet overdrafts.

## Features & Requirements Implemented

### Part A: Core Functionality
- **Authentication & Wallet:** JWT-based auth. Users automatically receive a wallet with starter credits upon registration.
- **SMS Dispatch (`POST /api/sms/send`):**
  - Validates and normalizes phone numbers.
  - Calculates GSM-7/Unicode segments and total cost.
  - **Concurrency Safety:** Uses `select_for_update()` database row locking inside atomic transactions to guarantee parallel requests cannot overspend the wallet.
  - **Priority Failover:** Routes messages through providers based on priority. Automatically fails over to the next route if the primary fails or lacks balance.
- **Refund System:** Implements an "upfront hold" mechanism. Credits are refunded automatically if a message fails on all routes, or if it succeeds on a cheaper route than the one originally held.
- **SMS Logs (`GET /api/sms/logs`):** Paginated history, filterable by status (`pending`, `sent`, `failed`), date, and phone number. Scoped strictly to the authenticated user.
- **Route Health (`GET /api/routes/health`):** Real-time metrics for mock providers including balance, 24-hour success rate, and a custom health score.
- **Mock Providers:** Three simulated routes (ROUTE_A, B, C) with configurable priorities, balances, and failure rates (e.g., ROUTE_A fails 30% of the time).

### Bonus Features Implemented
- **B2 - Rate Limiting:** Throttles API requests to 60 requests per minute per user to prevent abuse.
- **B4 - Unit Tests:** Comprehensive test suite (`sms/tests.py`) covering GSM-7/Unicode segment edge cases, phone validation, and a mocked integration test proving the failover logic.
- **B5 - Docker Compose:** Fully containerized setup. Run a single command to spin up the Django API, MSSQL Database, and Redis Queue infrastructure.

---

##  Architecture & Design Decisions

### 1. Synchronous vs. Asynchronous Processing
While modern gateways often use Celery/Redis (Bonus B1) for background processing, **this implementation intentionally uses a synchronous pipeline**. 
* **Why?** The primary goal of the concurrency stress test is to prove that parallel requests cannot overspend a wallet. By processing synchronously within a strict `transaction.atomic()` block with `select_for_update()`, we guarantee absolute financial safety and make the race-condition proof trivial to demonstrate.

### 2. True Success Rate Calculation
Instead of looking at the final `SMSLog` status (which hides failures that were recovered by failover), the health endpoint queries the `SMSDeliveryAttempt` table. This records *every single try*, providing a mathematically accurate success rate for each specific provider.

### 📊 Route Health Formula
The health score is calculated using the following weighted formula:
`Health Score = (Success Rate × 0.70) + (Balance Health × 0.30)`
* **Rationale:** A route might have a 100% success rate but be completely out of balance. Weighting balance at 30% ensures the system doesn't route traffic to a "healthy" but empty provider. (Assumes 5000 is the max starting balance).

---

##  API Endpoints

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/auth/register` | Register a new user (creates wallet with 100 credits). |
| `POST` | `/api/auth/login` | Authenticate and receive JWT access/refresh tokens. |
| `POST` | `/api/sms/send` | Send bulk SMS. Requires JWT. |
| `GET` | `/api/sms/logs` | View paginated, filterable SMS history. |
| `GET` | `/api/routes/health` | View provider balances, success rates, and health scores. |

### Example Payload: `POST /api/sms/send`
```json
{
  "to": ["9841234567", "+9779801122334"],
  "message": "Hello Nest Nepal!"
}