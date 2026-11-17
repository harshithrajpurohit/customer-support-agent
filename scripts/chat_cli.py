"""Interactive Customer Support Chat CLI.

Allows chatting with the Multi-Agent Customer Support system as any seeded customer:
  1: Aarav Sharma (Customer ID 1)
  2: Priya Verma  (Customer ID 2)
  3: Rohan Gupta  (Customer ID 3)
  4: Neha Iyer    (Customer ID 4)

Usage:
  Interactive mode:
    uv run python scripts/chat_cli.py

  Automated verification mode:
    uv run python scripts/chat_cli.py --verify
"""

from __future__ import annotations

import argparse
import sys
import uuid

# Ensure src/ is on PYTHONPATH
from pathlib import Path
_src = Path(__file__).resolve().parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from support.db import get_conn
from support.graph import process_message, resume_approval


CUSTOMERS = {
    1: {"name": "Aarav Sharma", "email": "aarav@example.com"},
    2: {"name": "Priya Verma", "email": "priya@example.com"},
    3: {"name": "Rohan Gupta", "email": "rohan@example.com"},
    4: {"name": "Neha Iyer", "email": "neha@example.com"},
}


def print_banner() -> None:
    print("=" * 65)
    print("   MULTI-AGENT CUSTOMER SUPPORT SYSTEM - CLI CHAT")
    print("=" * 65)
    print("Available Customers:")
    for cid, info in CUSTOMERS.items():
        print(f"  [{cid}] {info['name']} ({info['email']})")
    print("Commands:")
    print("  /switch  - Switch active customer")
    print("  /new     - Start fresh session")
    print("  /approve - Approve pending refund (simulate admin)")
    print("  /reject  - Reject pending refund (simulate admin)")
    print("  /exit    - Exit CLI")
    print("=" * 65)


def select_customer() -> int:
    while True:
        try:
            choice = input("\nSelect customer [1-4] (default 1): ").strip()
            if not choice:
                return 1
            cid = int(choice)
            if cid in CUSTOMERS:
                return cid
            print("Please enter a number between 1 and 4.")
        except (ValueError, KeyboardInterrupt, EOFError):
            return 1


def run_interactive() -> None:
    print_banner()
    customer_id = select_customer()
    customer = CUSTOMERS[customer_id]
    session_id = f"cli-{uuid.uuid4().hex[:8]}"

    print(f"\nLogged in as: {customer['name']} (ID: {customer_id})")
    print(f"Session ID:   {session_id}")
    print("Type your message and press Enter (or /exit to quit):\n")

    while True:
        try:
            user_input = input(f"{customer['name']}> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting. Goodbye!")
            break

        if not user_input:
            continue

        cmd = user_input.lower()
        if cmd in ("/exit", "/quit", "exit", "quit"):
            print("Goodbye!")
            break

        if cmd == "/switch":
            customer_id = select_customer()
            customer = CUSTOMERS[customer_id]
            session_id = f"cli-{uuid.uuid4().hex[:8]}"
            print(f"\nSwitched to: {customer['name']} (ID: {customer_id})")
            print(f"New Session ID: {session_id}\n")
            continue

        if cmd == "/new":
            session_id = f"cli-{uuid.uuid4().hex[:8]}"
            print(f"\nStarted new session: {session_id}\n")
            continue

        if cmd.startswith("/approve"):
            note = user_input[8:].strip() or "Approved via CLI"
            print(f"\n[Admin] Resuming approval for thread {session_id} with 'approve'...")
            res = resume_approval(session_id, decision="approve", note=note)
            print(f"Assistant: {res['reply']}\n")
            continue

        if cmd.startswith("/reject"):
            note = user_input[7:].strip() or "Rejected via CLI"
            print(f"\n[Admin] Resuming approval for thread {session_id} with 'reject'...")
            res = resume_approval(session_id, decision="reject", note=note)
            print(f"Assistant: {res['reply']}\n")
            continue

        # Normal message turn
        print("Thinking...")
        result = process_message(session_id, customer_id, user_input)

        status_tag = f"[{result['status'].upper()}]"
        print(f"\nAssistant {status_tag}:")
        print(f"{result['reply']}\n")

        if result.get("outcome") == "awaiting_approval":
            print(">>> Refund requires admin review. Type '/approve' or '/reject' to simulate admin.")


def run_verification() -> bool:
    """Run deterministic checks across all 5 required Milestone 6 flows."""
    print("=" * 60)
    print("RUNNING MILESTONE 6 VERIFICATION SUITE")
    print("=" * 60)

    # 1. Order lookup (1001) for Aarav (Customer 1)
    print("\n[Scenario 1] Order lookup: order 1001")
    s1 = f"verify-order-{uuid.uuid4().hex[:6]}"
    r1 = process_message(s1, customer_id=1, message="Where is my order 1001?")
    print(f"  Intent: {r1['intent']} | Status: {r1['status']}")
    print(f"  Reply: {r1['reply'][:100]}...")
    assert r1["intent"] == "order", f"Expected intent 'order', got {r1['intent']}"
    assert "1001" in r1["reply"] or "shipped" in r1["reply"].lower() or "transit" in r1["reply"].lower()
    print("  PASS: Order lookup verified.")

    # 2. Eligible refund (1002) -> creates pending refund
    print("\n[Scenario 2] Eligible refund: order 1002")
    s2 = f"verify-refund-{uuid.uuid4().hex[:6]}"
    # Clean any prior refund for 1002
    with get_conn() as conn:
        with conn.transaction():
            conn.execute("DELETE FROM refunds WHERE order_id = 1002")
    r2 = process_message(s2, customer_id=1, message="I want a refund for order 1002 because it was too small.")
    print(f"  Intent: {r2['intent']} | Status: {r2['status']} | Outcome: {r2['outcome']}")
    print(f"  Pending Refund ID: {r2.get('pending_refund_id')}")
    assert r2["status"] == "awaiting_approval", f"Expected awaiting_approval, got {r2['status']}"
    assert r2.get("pending_refund_id") is not None
    # Test resume
    r2_res = resume_approval(s2, decision="approve", note="All good")
    print(f"  Resumed Reply: {r2_res['reply']}")
    assert "approved" in r2_res["reply"].lower()
    print("  PASS: Eligible refund request & admin resume verified.")

    # 3. Ineligible refund (1003) -> explains outside window
    print("\n[Scenario 3] Ineligible refund: order 1003")
    s3 = f"verify-inelig-{uuid.uuid4().hex[:6]}"
    r3 = process_message(s3, customer_id=2, message="Can I get a refund for order 1003?")
    print(f"  Intent: {r3['intent']} | Status: {r3['status']}")
    print(f"  Reply: {r3['reply'][:120]}...")
    assert r3["intent"] == "refund"
    assert r3["status"] in ("answered", "resolved")
    print("  PASS: Ineligible refund explanation verified.")

    # 4. Angry message -> handoff
    print("\n[Scenario 4] Angry customer message -> Escalation Handoff")
    s4 = f"verify-angry-{uuid.uuid4().hex[:6]}"
    r4 = process_message(s4, customer_id=3, message="YOU SCAMMERS STOLE MY MONEY! I WILL SUE YOU IMMEDIATELY!")
    print(f"  Intent: {r4['intent']} | Status: {r4['status']} | Outcome: {r4['outcome']}")
    assert r4["status"] == "handed_off"
    assert r4["outcome"] == "handed_off"
    print("  PASS: Angry message handoff verified.")

    # 5. Two failed attempts -> handoff
    print("\n[Scenario 5] Two failed attempts -> Escalation Handoff")
    s5 = f"verify-failed-{uuid.uuid4().hex[:6]}"
    r5_1 = process_message(s5, customer_id=4, message="How do I configure quantum flux capacitor over Bluetooth?")
    print(f"  Turn 1 Status: {r5_1['status']}")
    r5_2 = process_message(s5, customer_id=4, message="You didn't answer. Explain hyperdrive warp matrix sync error now.")
    print(f"  Turn 2 Status: {r5_2['status']} | Outcome: {r5_2['outcome']}")
    assert r5_2["status"] == "handed_off"
    assert r5_2["outcome"] == "handed_off"
    print("  PASS: Consecutive failed attempts handoff verified.")

    print("\n" + "=" * 60)
    print("ALL 5 VERIFICATION SCENARIOS PASSED SUCCESSFULLY!")
    print("=" * 60)
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-Agent Customer Support CLI")
    parser.add_argument("--verify", action="store_true", help="Run automated verification checks")
    args = parser.parse_args()

    if args.verify:
        success = run_verification()
        sys.exit(0 if success else 1)
    else:
        run_interactive()
