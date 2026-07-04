import os
import pandas as pd
from datetime import datetime, timezone
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from models import Account, Transaction, FraudRing, FraudRingMember
from database import AsyncSessionLocal

def utcnow():
    return datetime.now(timezone.utc)

async def seed_database():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cleaned_csv = os.path.join(base_dir, "phase7", "cleaned", "cleaned_transactions.csv")
    
    # Try multiple analytics directories
    risk_scores_csv = None
    community_summaries_csv = None
    for d in ["analytics_final", "analytics", "analytics_v2"]:
        rcsv = os.path.join(base_dir, "phase8", d, "risk_scores.csv")
        ccsv = os.path.join(base_dir, "phase8", d, "community_summaries.csv")
        if os.path.exists(rcsv):
            risk_scores_csv = rcsv
            community_summaries_csv = ccsv
            break
            
    if not risk_scores_csv:
        risk_scores_csv = os.path.join(base_dir, "phase8", "analytics", "risk_scores.csv")
        community_summaries_csv = os.path.join(base_dir, "phase8", "analytics", "community_summaries.csv")

    if not os.path.exists(cleaned_csv) or not os.path.exists(risk_scores_csv):
        print(f"⚠ Missing cleaned transactions or risk scores CSVs at {cleaned_csv} or {risk_scores_csv}. Cannot seed database.")
        return

    async with AsyncSessionLocal() as db:
        # Check if database is already seeded
        existing_count = (await db.execute(select(func.count(Account.account_id)))).scalar_one()
        if existing_count > 0:
            print("✓ Database already seeded. Skipping initial seed.")
            return

        print("Seeding database with Phase 7 & 8 outputs...")

        # Load DataFrames
        risk_df = pd.read_csv(risk_scores_csv, dtype=str)
        risk_df = risk_df.fillna("")

        cleaned_df = pd.read_csv(cleaned_csv, dtype=str)
        cleaned_df = cleaned_df.fillna("")

        # Create FraudRings from community summaries if present
        communities = {}
        if os.path.exists(community_summaries_csv):
            comm_df = pd.read_csv(community_summaries_csv, dtype=str)
            for _, row in comm_df.iterrows():
                ring_id = row.get("community_id", "")
                if not ring_id:
                    continue
                size = int(row.get("size", 0))
                total_flow = float(row.get("total_flow", 0.0))
                ring = FraudRing(
                    ring_id=ring_id,
                    typology="louvain_community",
                    status="detected",
                    confidence_score=0.75,
                    total_accounts=size,
                    total_amount_moved=total_flow,
                )
                db.add(ring)
                communities[ring_id] = ring

        await db.flush()

        # Create Accounts
        accounts_map = {}
        for _, row in risk_df.iterrows():
            acct_id = row["account_id"]
            risk_score = float(row.get("risk_score", 0.0))
            risk_tier = row.get("risk_tier", "LOW")
            is_suspect = risk_tier in ("HIGH", "CRITICAL")
            
            # Determine role based on features
            role = "Mule"
            if row.get("flag_fan_in", "").lower() == "true":
                role = "Collector"
            elif row.get("flag_fan_out", "").lower() == "true":
                role = "Distributor"

            acct = Account(
                account_id=acct_id,
                holder_name=row.get("account_holder", "") or "Unknown",
                bank_name=row.get("bank_name", "") or "Unknown",
                risk_score=risk_score,
                is_suspect=is_suspect,
                fraud_role=role,
                fraud_ring_id=row.get("community_id", ""),
                last_scored_at=utcnow(),
            )
            db.add(acct)
            accounts_map[acct_id] = acct

            # Link membership
            comm_id = row.get("community_id", "")
            if comm_id:
                member = FraudRingMember(
                    ring_id=comm_id,
                    account_id=acct_id,
                    role_in_ring=role.lower(),
                    amount_handled=0.0
                )
                db.add(member)

        await db.flush()

        # Create Transactions
        for _, row in cleaned_df.iterrows():
            def _f(v, default=""):
                val = row.get(v, "")
                if val == "" or (isinstance(val, float) and pd.isna(val)):
                    return default
                return val

            def _float(v):
                try:
                    return float(_f(v, 0.0))
                except (ValueError, TypeError):
                    return 0.0

            def _bool(v):
                val = str(_f(v, "False")).lower()
                return val in ("true", "1", "yes")

            acct_id = _f("account_id")
            
            txn = Transaction(
                account_id=acct_id,
                date=_f("date"),
                time=_f("time", "00:00:00"),
                narration=_f("narration"),
                channel=_f("channel", "OTHER"),
                debit=_float("debit"),
                credit=_float("credit"),
                balance=_float("balance"),
                utr_ref=_f("utr_ref"),
                counterparty_account_id=_f("counterparty_account_id", None),
                counterparty_name=_f("counterparty_name", None),
                source_file=_f("source_file"),
                source_format=_f("source_format"),
                ingestion_warnings=_f("ingestion_warnings"),
                clean_flags=_f("clean_flags"),
                is_duplicate=_bool("is_duplicate"),
                is_balance_breach=_bool("is_balance_breach"),
                is_high_value_flag=_bool("is_high_value_flag"),
                is_ocr_row=_bool("is_ocr_row"),
                final_risk_score=float(accounts_map[acct_id].risk_score) if acct_id in accounts_map else 0.0
            )
            db.add(txn)

        await db.commit()
        print("✓ Database successfully seeded with Phase 7 & 8 outputs.")
