import asyncio
import datetime as dt
import discord

from .db import connect


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


async def snapshot_invites_to_db(guild: discord.Guild) -> None:
    invites = await guild.invites()
    now = _now_iso()

    async with connect() as db:
        for inv in invites:
            inviter_id = inv.inviter.id if inv.inviter else None
            created_at = inv.created_at.isoformat() if inv.created_at else None
            uses = inv.uses or 0

            await db.execute(
                """
                INSERT INTO invite_baseline (guild_id, code, uses, inviter_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, code) DO UPDATE SET
                  uses=excluded.uses,
                  inviter_id=excluded.inviter_id,
                  created_at=excluded.created_at,
                  updated_at=excluded.updated_at
                """,
                (guild.id, inv.code, uses, inviter_id, created_at, now),
            )
        await db.commit()


async def _detect_used_invite_once(guild: discord.Guild) -> dict | None:
    invites = await guild.invites()

    async with connect() as db:
        rows = await db.execute_fetchall(
            "SELECT code, uses, inviter_id FROM invite_baseline WHERE guild_id = ?",
            (guild.id,),
        )
        baseline = {r[0]: (r[1], r[2]) for r in rows}  # code -> (uses, inviter_id)

        best = None
        for inv in invites:
            code = inv.code
            after = inv.uses or 0
            before, inviter_id = baseline.get(code, (0, inv.inviter.id if inv.inviter else None))
            delta = after - before
            if delta <= 0:
                continue

            if best is None or delta > best["delta"]:
                best = {
                    "code": code,
                    "inviter_id": inviter_id if inviter_id is not None else (inv.inviter.id if inv.inviter else None),
                    "before": before,
                    "after": after,
                    "delta": delta,
                }

        # Update baseline to latest so future joins compare correctly
        now = _now_iso()
        for inv in invites:
            inviter_id = inv.inviter.id if inv.inviter else None
            created_at = inv.created_at.isoformat() if inv.created_at else None
            uses = inv.uses or 0

            await db.execute(
                """
                INSERT INTO invite_baseline (guild_id, code, uses, inviter_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, code) DO UPDATE SET
                  uses=excluded.uses,
                  inviter_id=excluded.inviter_id,
                  created_at=excluded.created_at,
                  updated_at=excluded.updated_at
                """,
                (guild.id, inv.code, uses, inviter_id, created_at, now),
            )
        await db.commit()

    if best is None:
        return None

    return {
        "code": best["code"],
        "inviter_id": best["inviter_id"],
        "before": best["before"],
        "after": best["after"],
    }


async def detect_used_invite(guild: discord.Guild) -> dict | None:
    """
    Try twice to reduce race issues (especially with max_uses=1 invites).
    """
    first = await _detect_used_invite_once(guild)
    if first is not None:
        return first

    # Small delay to let Discord bump invite uses
    await asyncio.sleep(1.0)

    return await _detect_used_invite_once(guild)


async def log_join_event(*, guild_id: int, member: discord.Member, invite_info: dict | None) -> None:
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO invite_join_log (
              guild_id, member_id, member_tag, joined_at,
              invite_code, inviter_id, uses_before, uses_after
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                member.id,
                str(member),
                _now_iso(),
                (invite_info["code"] if invite_info else None),
                (invite_info["inviter_id"] if invite_info else None),
                (invite_info["before"] if invite_info else None),
                (invite_info["after"] if invite_info else None),
            ),
        )
        await db.commit()
