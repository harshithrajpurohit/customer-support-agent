# 🤖 Multi-Agent Customer Support System

A production-grade, **multi-agent AI customer support bot** for a mock e-commerce store — built with **LangGraph** orchestration, an **MCP tool server**, **human-in-the-loop** refund approval, and a full-stack **FastAPI + React** interface.

## ✨ Key Features

- **Multi-Agent Architecture** — Router classifies intent and sentiment, then dispatches to Orders, Refund, Technical, or Handoff specialist agents
- **MCP (Model Context Protocol) Tool Server** — Agents interact with the database via a secure stdio-transport MCP server; tools are discovered dynamically
- **Deterministic Policy Engine** — Pure-function refund eligibility checks (14-day window, HIGH_VALUE flag, duplicate prevention) with zero LLM involvement
- **Human-in-the-Loop Approval** — Every refund request pauses via LangGraph `interrupt()` and awaits admin approval; money never moves without a human
- **Secure `customer_id` Injection** — The LLM never sees or controls which customer's data it accesses; `customer_id` is stripped from tool schemas and injected server-side
- **Prompt Injection Defense** — All agent prompts treat user messages and tool outputs as untrusted DATA, never instructions
- **Redis Session Memory** — Sliding-window conversation history (last 20 messages) with TTL; graceful in-memory fallback
- **Langfuse Observability** — Full trace/span/generation logging for every LLM call; silent no-op fallback when keys are absent
- **Admin Dashboard** — React portal for refund approvals (with HIGH_VALUE badges), handoff inbox, and resolution tracking
- **50-Scenario Evaluation Suite** — Deterministic scripted evals covering order tracking, refunds, escalation, security/adversarial, and multi-turn conversations

---

## 🏗️ Architecture

```mermaid
flowchart TD
    CW["💬 Customer Chat Widget<br/>(React @ :5173)"] -->|HTTP POST /api/chat| API["⚡ FastAPI Backend<br/>(:8000)"]
    AP["🔧 Admin Portal<br/>(React /admin)"] -->|"X-Admin-Token Header"| API

    API -->|Rate Limit + Session Check| GR["🧠 LangGraph State Machine"]

    subgraph LangGraph["LangGraph Orchestration"]
        GR --> LS["load_session"]
        LS -->|"Conditional"| RT["Router Agent<br/>(Structured Output)"]
        RT -->|"intent=order"| OA["Orders Agent"]
        RT -->|"intent=refund"| RA["Refund Agent"]
        RT -->|"intent=technical"| TA["Technical Agent"]
        RT -->|"angry / wants_human / complaint"| HO["Handoff Agent"]
        OA --> SPD{"Specialist Post-Decision"}
        RA --> SPD
        TA --> SPD
        SPD -->|"pending_refund_id"| AG["⏸️ Approval Gate<br/>(interrupt)"]
        SPD -->|"failed ≥ 2"| HO
        SPD -->|"resolved"| SR["save_and_respond"]
        AG --> SR
        HO --> SR
        SR --> END["END"]
    end

    subgraph Tools["MCP Tool Layer"]
        OA -->|"Tool Calls"| MC["MCP Client<br/>(customer_id injected)"]
        RA -->|"Tool Calls"| MC
        TA -->|"search_kb"| MC
        MC -->|"stdio transport"| MS["MCP Server<br/>(FastMCP)"]
        MS --> TC["tools_core.py"]
        TC --> PE["Policy Engine<br/>(Pure Functions)"]
        TC --> DB[("PostgreSQL")]
        TC --> KB["FAQ Knowledge Base"]
    end

    subgraph Storage["Storage & Observability"]
        API --- Redis[("Redis / In-Memory<br/>Session Store")]
        API --- CP[("PostgresSaver /<br/>MemorySaver Checkpoint")]
        API --- LF["Langfuse Tracing<br/>(No-op Fallback)"]
    end
```

---

## 🛠️ Tech Stack

| Technology | Role |
|---|---|
| **Groq** (LLM API) | Fast inference for all agent LLM calls (`openai/gpt-oss-120b`) |
| **LangGraph** | State machine orchestration with conditional routing and `interrupt()` |
| **MCP** (Model Context Protocol) | Stdio-transport tool server for secure, discoverable tool invocation |
| **PostgreSQL** | Persistent storage for customers, orders, refunds, chat messages, handoffs |
| **Redis** | Session memory store with TTL (optional; falls back to in-memory) |
| **Langfuse** | LLM observability — traces, spans, generations (optional; falls back to no-op) |
| **FastAPI** | Backend REST API with Pydantic validation, CORS, rate limiting |
| **React** (Vite) | Customer chat widget + admin portal SPA |
| **Pydantic v2** | Request/response schemas, structured LLM output validation, policy models |
| **psycopg 3** | Async-ready PostgreSQL driver with connection pooling |
| **python-dotenv** | Environment variable management |

---

## 🚀 Setup (Windows PowerShell)

### 1. Install Dependencies

```powershell
uv sync
```

### 2. Create PostgreSQL User and Database

```powershell
# Open psql as the postgres superuser
psql -U postgres

# Inside psql:
CREATE USER support_user WITH PASSWORD 'your_password_here';
CREATE DATABASE support_db OWNER support_user;
GRANT ALL PRIVILEGES ON DATABASE support_db TO support_user;
\q
```

### 3. Configure Environment Variables

Create a `.env` file in the **parent directory** (`c:\Users\lenovo\harshith-ai-engineer\.env`) with these variables:

| Variable | Description |
|---|---|
| `GROQ_API_KEY` | **Required.** Your Groq API key for LLM inference |
| `DATABASE_URL` | **Required.** PostgreSQL connection string, e.g. `postgresql://support_user:password@localhost:5432/support_db` |
| `REDIS_URL` | Optional. Redis connection URL (e.g. `redis://localhost:6379`). Falls back to in-memory if unset |
| `LANGFUSE_PUBLIC_KEY` | Optional. Langfuse public key for tracing. Tracing disabled if unset |
| `LANGFUSE_SECRET_KEY` | Optional. Langfuse secret key for tracing |
| `LANGFUSE_HOST` | Optional. Langfuse host URL (defaults to Langfuse Cloud) |
| `ADMIN_TOKEN` | Optional. Token for admin API endpoints. Auto-generated and printed to stderr if unset |

> **⚠️ Never commit actual secret values.** See `.env.example` for the template.

### 4. Initialize and Seed the Database

```powershell
uv run python db/init_db.py
```

This creates all tables (`customers`, `orders`, `refunds`, `chat_messages`, `handoffs`) and seeds demo data.

### 5. Start the Backend

```powershell
# Option A: via main.py
uv run python main.py

# Option B: via uvicorn directly
uv run uvicorn support.api:app --port 8000
```

The API will be available at `http://localhost:8000`. Swagger docs at `/docs`.

### 6. Start the Frontend

```powershell
cd frontend
npm install
npm run dev
```

Frontend runs at `http://localhost:5173`.

### 7. Run Tests

```powershell
uv run pytest
```

### 8. Run Evaluations

```powershell
uv run python evals/run_evals.py
```

Runs all 50 scripted evaluation scenarios and generates JSON + Markdown reports in `evals/reports/`.

---

## 💬 How to Use

### Customer Chat

1. Open `http://localhost:5173` in your browser
2. Select a customer from the **login dropdown** (demo customers are pre-seeded: Aarav, Priya, etc.)
3. Start chatting — ask about orders, request refunds, or ask general questions
4. **Order tracking**: "Where is my order 1001?" → Agent calls `get_order_status` and responds with real data
5. **Refund requests**: "I want a refund for order 1002" → Agent checks eligibility via policy engine → If eligible, creates a pending refund request → Status shows `awaiting_approval`
6. **Technical/FAQ**: "How do I reset my password?" → Agent searches the knowledge base and responds

### What Happens with Refund Requests

1. The refund agent calls `check_refund_eligibility` (14-day window, delivered status, no duplicate refunds)
2. If eligible, `create_refund_request` inserts a `pending` record — **the agent never approves money**
3. The LangGraph pauses via `interrupt()` at the Approval Gate
4. An admin must approve or reject via the Admin Portal
5. The customer is notified of the decision

---

## 🔐 Admin Screen

1. Navigate to `http://localhost:5173/admin`
2. Enter your **Admin Token** (printed to stderr on startup, or the value of `ADMIN_TOKEN` in `.env`)
3. **Refund Approvals** tab:
   - See all pending refund requests with customer name, order, amount, reason
   - Orders above ₹20,000 show a **HIGH_VALUE** badge for extra scrutiny
   - Click **Approve** or **Reject** — the customer receives a notification in their chat
4. **Handoff Inbox** tab:
   - See all open escalations (angry customers, explicit human requests, repeated failures)
   - View conversation transcript and escalation summary
   - Click **Resolve** to mark as handled

---

## ⚠️ Project Limitations

- **Demo authentication** — Login is email-based lookup only; no real auth provider (OAuth, JWT, etc.)
- **Mock seed data** — Pre-seeded customers and orders; not connected to a real e-commerce backend
- **Redis fallback** — If Redis is unavailable, sessions use in-memory storage (lost on restart)
- **No Docker** — Designed for local development with direct PostgreSQL/Redis connections
- **No external channels** — No WhatsApp, SMS, or email channel integrations
- **LLM non-determinism** — Some eval scenarios (especially routing edge cases) may vary between runs due to inherent LLM variability

---

## 📄 Resume Bullets

- Built a multi-agent customer support system with LangGraph orchestration, MCP tool server, and human-in-the-loop approval; achieved **88% eval pass rate** across 50 scripted scenarios
- Maintained **76% auto-resolution rate** with deterministic policy engine and secure customer_id injection, with **0 safety violations** (no unauthorized refunds or cross-customer data leaks)
- Full-stack delivery: FastAPI backend, React chat widget + admin portal, PostgreSQL + Redis session store, Langfuse observability, 50-scenario evaluation suite
