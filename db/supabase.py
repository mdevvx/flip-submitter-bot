import os
from supabase import create_client, Client
from logger import get_logger
from datetime import datetime

logger = get_logger("supabase")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    logger.error("SUPABASE_URL or SUPABASE_KEY missing in environment variables.")
    raise RuntimeError("Supabase credentials not set (SUPABASE_URL / SUPABASE_KEY).")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# DB wrapper functions with basic error handling
def insert_flip(flip: dict):
    try:
        res = supabase.table("flips").insert(flip).execute()
        logger.debug("insert_flip result: %s", res)
        # Try to return the inserted row(s) data robustly
        try:
            data = getattr(res, "data", None) or (
                res.get("data") if isinstance(res, dict) else None
            )
            if data:
                # return the first inserted row (most uses expect dict)
                return data[0] if isinstance(data, list) else data
        except Exception:
            pass
        # Fallback: return whole res
        return res
    except Exception as e:
        logger.exception("Failed to insert flip: %s", e)
        raise


def get_pending_flips(guild_id: int):
    try:
        # pass boolean for ascending (True = ascending)
        res = (
            supabase.table("flips")
            .select("*")
            .eq("guild_id", guild_id)
            .eq("status", "pending")
            .order("submitted_at", desc=False)  # ascending = True
            .execute()
        )
        return res.data or []
    except Exception as e:
        logger.exception("Failed to fetch pending flips: %s", e)
        return []


def update_flip(flip_id: str, changes: dict):
    try:
        # If handled_at is provided as 'now()', replace with actual timestamp
        if changes.get("handled_at") == "now()":
            changes["handled_at"] = datetime.utcnow().isoformat()
        res = supabase.table("flips").update(changes).eq("id", flip_id).execute()
        logger.debug("update_flip result: %s", res)
        return res
    except Exception as e:
        logger.exception("Failed to update flip: %s", e)
        raise


def add_user_profit(guild_id: int, user_id: int, username: str, profit: float):
    try:
        guild_id = int(guild_id)
        user_id = int(user_id)
        profit = float(profit or 0.0)

        # Try fetching existing record
        existing = (
            supabase.table("users")
            .select("*")
            .eq("id", user_id)
            .eq("guild_id", guild_id)
            .execute()
        )

        if existing and getattr(existing, "data", None):
            current = existing.data[0]
            current_total = float(current.get("total_profit") or 0.0)
            new_total = current_total + profit
            supabase.table("users").update(
                {"total_profit": new_total, "username": username}
            ).eq("id", user_id).eq("guild_id", guild_id).execute()
            logger.debug(
                "Updated user %s in guild %s total_profit -> %s",
                user_id,
                guild_id,
                new_total,
            )
        else:
            # New insert or upsert
            row = {
                "id": user_id,
                "guild_id": guild_id,
                "username": username,
                "total_profit": profit,
            }
            try:
                supabase.table("users").upsert(row).execute()
            except Exception:
                supabase.table("users").insert(row).execute()

            logger.debug(
                "Inserted new user %s in guild %s with profit %s",
                user_id,
                guild_id,
                profit,
            )

    except Exception as e:
        logger.exception("Failed to add user profit: %s", e)
        raise


def get_leaderboard_top(guild_id: int, limit: int = 10):
    try:
        # order by total_profit descending => second arg False (ascending=False)
        res = (
            supabase.table("users")
            .select("*")
            .eq("guild_id", guild_id)
            .order("total_profit", desc=True)  # ascending = False -> descending order
            .limit(limit)
            .execute()
        )
        return res.data or []
    except Exception as e:
        logger.exception("Failed to get leaderboard: %s", e)
        return []


def ensure_guild_settings(guild_id: int):
    try:
        res = (
            supabase.table("guild_settings")
            .select("*")
            .eq("guild_id", guild_id)
            .execute()
        )
        if res.data:
            return res.data[0]
        else:
            supabase.table("guild_settings").insert({"guild_id": guild_id}).execute()
            return {"guild_id": guild_id}
    except Exception as e:
        logger.exception("Failed to ensure guild settings: %s", e)
        # fallback minimal settings
        return {"guild_id": guild_id}


# Simple ping to check the connection
def ping():
    try:
        # select now from pg to test connectivity via RPC
        res = supabase.rpc("now").execute()  # might fail; fallback to a simple select
        return True, "OK"
    except Exception:
        try:
            res = supabase.table("guild_settings").select("guild_id").limit(1).execute()
            return True, "OK"
        except Exception as e:
            logger.exception("Supabase ping failed: %s", e)
            return False, str(e)
