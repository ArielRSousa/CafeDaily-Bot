from __future__ import annotations

import os
from datetime import date, datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from src import metrics_service as ms
from src.db import fetch_submitted_at_by_user_for_guild, fetch_submitted_at_for_user
from src.daily_view import _EMBED_BRAND, _timezone


async def _daily_stats_handler(
    interaction: discord.Interaction,
    membro: discord.Member,
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "Use este comando dentro de um servidor.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    tz = _timezone()
    rows = await fetch_submitted_at_for_user(interaction.guild.id, membro.id)

    if not rows:
        await interaction.followup.send(
            f"{membro.mention} ainda não tem Dailies registradas neste servidor.",
            ephemeral=True,
        )
        return

    dates = ms.distinct_sorted_dates(rows, tz)
    today_local = datetime.now(timezone.utc).astimezone(tz).date()
    streak_last = ms.streak_ending_on_last_submission(dates)
    streak_today = ms.streak_including_today(dates, today_local)
    longest = ms.longest_streak_ever(dates)
    hour_text, top_hours = ms.hour_stats(rows, tz)

    total = len(rows)
    first = dates[0]
    last = dates[-1]
    sent_today = last == today_local

    top_h_str = "\n".join(f"`{h:02d}h` — {c}×" for h, c in top_hours) if top_hours else "—"

    embed = discord.Embed(
        title="Métricas de Daily",
        description=f"Membro: {membro.mention}\nFuso: `{tz.key}`",
        color=_EMBED_BRAND,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url=membro.display_avatar.url)
    embed.add_field(name="Total de envios", value=str(total), inline=True)
    embed.add_field(name="Dias com pelo menos 1 daily", value=str(len(dates)), inline=True)
    embed.add_field(
        name="Sequência (até o último envio)",
        value=(
            f"**{streak_last}** dia(s) seguidos\n"
            f"Último registro: `{last.strftime('%d/%m/%Y')}`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Sequência “viva” (inclui hoje)",
        value=(
            f"**{streak_today}** dia(s) — só conta se enviou **hoje** (`{today_local.strftime('%d/%m/%Y')}`)\n"
            f"Enviou hoje: **{'sim' if sent_today else 'não'}**"
        ),
        inline=False,
    )
    embed.add_field(name="Recorde de dias seguidos", value=f"**{longest}**", inline=True)
    embed.add_field(
        name="Primeiro dia registrado",
        value=f"`{first.strftime('%d/%m/%Y')}`",
        inline=True,
    )
    embed.add_field(
        name="Horário habitual",
        value=f"{hour_text}\n\n**Mais frequentes:**\n{top_h_str}",
        inline=False,
    )
    embed.set_footer(text="CafeDaily · gestão")

    await interaction.followup.send(embed=embed, ephemeral=True)


async def _daily_rank_streaks_handler(
    interaction: discord.Interaction,
    limite: int,
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "Use este comando dentro de um servidor.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    tz = _timezone()
    by_user = await fetch_submitted_at_by_user_for_guild(interaction.guild.id)

    ranked: list[tuple[int, int, date]] = []
    for uid, ts_list in by_user.items():
        d_list = ms.distinct_sorted_dates(ts_list, tz)
        if not d_list:
            continue
        streak = ms.streak_ending_on_last_submission(d_list)
        ranked.append((uid, streak, d_list[-1]))

    ranked.sort(key=lambda x: (-x[1], -x[2].toordinal()))
    ranked = ranked[:limite]

    if not ranked:
        await interaction.followup.send(
            "Nenhuma Daily registrada neste servidor ainda.",
            ephemeral=True,
        )
        return

    guild = interaction.guild
    lines: list[str] = []
    for i, (uid, streak, last_d) in enumerate(ranked, start=1):
        member = guild.get_member(uid) if guild else None
        if member is not None:
            label = discord.utils.escape_markdown(member.display_name)
        else:
            label = f"usuário {uid}"
        lines.append(
            f"`{i:>2}.` **{streak}** dia(s) — {label} · último: `{last_d.strftime('%d/%m/%Y')}`",
        )

    body = "\n".join(lines)
    if len(body) > 3800:
        body = body[:3797] + "…"

    embed = discord.Embed(
        title="Ranking — sequência de dias com Daily",
        description=body,
        color=_EMBED_BRAND,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text=f"CafeDaily · top {len(ranked)} · fuso {tz.key}")
    await interaction.followup.send(embed=embed, ephemeral=True)


def register_metrics_commands(bot: commands.Bot) -> None:
    guild_obj = discord.Object(id=int(os.environ["DISCORD_GUILD_ID"])) if os.getenv("DISCORD_GUILD_ID") else None

    @app_commands.describe(
        membro="Quem você quer analisar",
    )
    async def daily_stats(interaction: discord.Interaction, membro: discord.Member) -> None:
        await _daily_stats_handler(interaction, membro)

    w_stats = app_commands.default_permissions(administrator=True)(daily_stats)
    if guild_obj is not None:
        w_stats = app_commands.guilds(guild_obj)(w_stats)
    bot.tree.command(
        name="daily-stats",
        description="Métricas de Daily: sequência, horários e totais (somente administradores).",
    )(w_stats)

    @app_commands.describe(limite="Quantos colocados no ranking (3 a 25).")
    async def daily_ranking_streaks(
        interaction: discord.Interaction,
        limite: app_commands.Range[int, 3, 25] = 10,
    ) -> None:
        await _daily_rank_streaks_handler(interaction, limite)

    w_rank = app_commands.default_permissions(administrator=True)(daily_ranking_streaks)
    if guild_obj is not None:
        w_rank = app_commands.guilds(guild_obj)(w_rank)
    bot.tree.command(
        name="daily-ranking-streaks",
        description="Ranking por dias consecutivos com Daily até o último envio (somente administradores).",
    )(w_rank)
