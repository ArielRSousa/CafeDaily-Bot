from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands

from src.db import insert_daily, set_admin_message_id

logger = logging.getLogger(__name__)

BUTTON_CUSTOM_ID = "cafedaily:register_daily"
PAINEL_IMAGE_PATH = Path(__file__).resolve().parent / "assets" / "painel_de_daily.png"
PAINEL_IMAGE_FILENAME = "painel_de_daily.png"

# Discord: ~6000 caracteres no total por embed; valor de campo ≤ 1024; ≤ 25 campos; ≤ 10 embeds por mensagem.
_EMBED_CHAR_SOFT_LIMIT = 5200
_EMBED_FIELD_VALUE_MAX = 1024
_MAX_EMBEDS_PER_MESSAGE = 10
_EMBED_BRAND = discord.Color(0x2B140F)  # #2B140F


def _timezone() -> ZoneInfo:
    tz_name = os.environ.get("TIMEZONE", "America/Sao_Paulo")
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        logger.warning("TIMEZONE inválido (%s); usando UTC.", tz_name)
        return ZoneInfo("UTC")


def _chunk_field_value(text: str, max_len: int = _EMBED_FIELD_VALUE_MAX) -> list[str]:
    text = text.strip() or "—"
    return [text[i : i + max_len] for i in range(0, len(text), max_len)]


def _field_rows(label: str, text: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    chunks = _chunk_field_value(text)
    for idx, chunk in enumerate(chunks):
        name = label if idx == 0 else f"{label} ({idx + 1})"
        rows.append((name[:256], chunk[:_EMBED_FIELD_VALUE_MAX]))
    return rows


def _embed_static_size(embed: discord.Embed) -> int:
    n = len(embed.title or "") + len(embed.description or "")
    if embed.author and embed.author.name:
        n += len(embed.author.name)
    if embed.footer and embed.footer.text:
        n += len(embed.footer.text)
    return n


def _build_admin_daily_embeds(
    *,
    project: str,
    yesterday: str,
    today: str,
    tomorrow: str,
    blockers: str,
    user: discord.abc.User,
    submitted_at: datetime,
) -> list[discord.Embed]:
    tz = _timezone()
    if submitted_at.tzinfo is None:
        submitted_at = submitted_at.replace(tzinfo=timezone.utc)
    local = submitted_at.astimezone(tz)
    date_str = local.strftime("%d/%m/%Y")
    time_str = local.strftime("%H:%M")

    title = f"Daily Report — {project} — {date_str}"
    if len(title) > 256:
        title = f"{title[:252]}…"

    description = (
        f"Registrado por <@{user.id}> às **{time_str}** · `{tz.key}`\n"
        f"Usuário: **{discord.utils.escape_markdown(user.display_name)}** (`{user.id}`)"
    )[:4096]

    pairs: list[tuple[str, str]] = []
    pairs.extend(_field_rows("O que fiz ontem", yesterday))
    pairs.extend(_field_rows("O que farei hoje", today))
    pairs.extend(_field_rows("O que farei amanhã", tomorrow))
    pairs.extend(_field_rows("Impedimentos", blockers))

    embeds: list[discord.Embed] = []
    current: discord.Embed | None = None
    is_first = True
    size_budget = 0

    def _start_embed(*, first: bool) -> discord.Embed:
        e = discord.Embed(
            color=_EMBED_BRAND,
            timestamp=submitted_at,
        )
        if first:
            e.title = title
            e.description = description
            e.set_author(name=user.display_name[:256], icon_url=user.display_avatar.url)
        else:
            e.title = "Daily Report (continuação)"[:256]
        e.set_footer(text="CafeDaily · relatório de Daily")
        return e

    for name, value in pairs:
        need_new = current is None
        if current is not None:
            projected = size_budget + len(name) + len(value)
            if len(current.fields) >= 25 or projected > _EMBED_CHAR_SOFT_LIMIT:
                need_new = True

        if need_new:
            if current is not None:
                embeds.append(current)
            current = _start_embed(first=is_first)
            is_first = False
            size_budget = _embed_static_size(current)

        assert current is not None
        current.add_field(name=name, value=value, inline=False)
        size_budget += len(name) + len(value)

    if current is not None:
        embeds.append(current)

    return embeds


class DailyModal(discord.ui.Modal, title="Registrar Daily"):
    project = discord.ui.TextInput(
        label="Qual projeto?",
        style=discord.TextStyle.short,
        max_length=400,
        required=True,
        placeholder="Ex.: Trajano PMP Digital",
    )
    yesterday = discord.ui.TextInput(
        label="O que fiz ontem",
        style=discord.TextStyle.paragraph,
        max_length=4000,
        required=True,
    )
    today = discord.ui.TextInput(
        label="O que farei hoje",
        style=discord.TextStyle.paragraph,
        max_length=4000,
        required=True,
    )
    tomorrow = discord.ui.TextInput(
        label="O que farei amanhã",
        style=discord.TextStyle.paragraph,
        max_length=4000,
        required=True,
    )
    blockers = discord.ui.TextInput(
        label="Impedimentos",
        style=discord.TextStyle.paragraph,
        max_length=4000,
        required=True,
        placeholder="Ex.: Não tenho impedimentos.",
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        project = str(self.project.value).strip()
        yesterday = str(self.yesterday.value).strip()
        today = str(self.today.value).strip()
        tomorrow = str(self.tomorrow.value).strip()
        blockers = str(self.blockers.value).strip()

        if not all([project, yesterday, today, tomorrow, blockers]):
            await interaction.followup.send(
                "Todos os campos são obrigatórios. Tente novamente.",
                ephemeral=True,
            )
            return

        admin_raw = os.environ.get("DAILY_ADMIN_CHANNEL_ID")
        if not admin_raw:
            logger.error("DAILY_ADMIN_CHANNEL_ID não configurado.")
            await interaction.followup.send(
                "O servidor não está configurado corretamente (canal admin). Avise um administrador.",
                ephemeral=True,
            )
            return

        try:
            admin_channel_id = int(admin_raw)
        except ValueError:
            logger.error("DAILY_ADMIN_CHANNEL_ID inválido.")
            await interaction.followup.send(
                "Configuração inválida do canal admin. Avise um administrador.",
                ephemeral=True,
            )
            return

        admin_channel = interaction.client.get_channel(admin_channel_id)
        if admin_channel is None:
            try:
                admin_channel = await interaction.client.fetch_channel(admin_channel_id)
            except discord.HTTPException:
                admin_channel = None

        if not isinstance(admin_channel, discord.abc.Messageable):
            logger.error("Canal admin %s não encontrado ou sem permissão.", admin_channel_id)
            await interaction.followup.send(
                "Não consegui acessar o canal de administradores. Avise a staff.",
                ephemeral=True,
            )
            return

        submitted_at = datetime.now(timezone.utc)
        guild_id = interaction.guild.id if interaction.guild else 0

        try:
            daily_id = await insert_daily(
                guild_id=guild_id,
                user_id=interaction.user.id,
                username=str(interaction.user),
                project=project,
                yesterday=yesterday,
                today=today,
                tomorrow=tomorrow,
                blockers=blockers,
                submitted_at=submitted_at,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao gravar daily no MongoDB.")
            await interaction.followup.send(
                "Não consegui salvar sua Daily no banco de dados. Tente de novo em instantes.",
                ephemeral=True,
            )
            return

        admin_embeds = _build_admin_daily_embeds(
            project=project,
            yesterday=yesterday,
            today=today,
            tomorrow=tomorrow,
            blockers=blockers,
            user=interaction.user,
            submitted_at=submitted_at,
        )

        try:
            first_msg: discord.Message | None = None
            for start in range(0, len(admin_embeds), _MAX_EMBEDS_PER_MESSAGE):
                batch = admin_embeds[start : start + _MAX_EMBEDS_PER_MESSAGE]
                send_kwargs: dict[str, object] = {"embeds": batch}
                if start == 0:
                    send_kwargs["content"] = "@everyone"
                    send_kwargs["allowed_mentions"] = discord.AllowedMentions(everyone=True)
                msg = await admin_channel.send(**send_kwargs)
                if first_msg is None:
                    first_msg = msg
            if first_msg is not None:
                await set_admin_message_id(daily_id, first_msg.id)
        except discord.HTTPException:
            logger.exception("Falha ao enviar mensagem ao canal admin.")
            await interaction.followup.send(
                "Sua Daily foi salva, mas não consegui notificar o canal de administradores.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            "Daily registrada com sucesso. Obrigado!",
            ephemeral=True,
        )


class DailyPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Registrar Daily",
        style=discord.ButtonStyle.green,
        custom_id=BUTTON_CUSTOM_ID,
    )
    async def register_daily(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(DailyModal())


async def _daily_setup_handler(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "Use este comando dentro de um servidor.",
            ephemeral=True,
        )
        return

    if interaction.channel is None or not isinstance(
        interaction.channel,
        discord.abc.Messageable,
    ):
        await interaction.response.send_message(
            "Não consigo publicar o painel neste contexto.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    if not PAINEL_IMAGE_PATH.is_file():
        await interaction.followup.send(
            "O arquivo do painel não foi encontrado em `src/assets/painel_de_daily.png`. "
            "Confira se a imagem existe no servidor do bot.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(color=_EMBED_BRAND)
    embed.set_image(url=f"attachment://{PAINEL_IMAGE_FILENAME}")
    panel_file = discord.File(PAINEL_IMAGE_PATH, filename=PAINEL_IMAGE_FILENAME)

    await interaction.channel.send(
        embed=embed,
        view=DailyPanelView(),
        file=panel_file,
    )
    await interaction.followup.send(
        "Painel publicado neste canal.",
        ephemeral=True,
    )


def register_commands(bot: commands.Bot) -> None:
    wrapped = app_commands.default_permissions(administrator=True)(_daily_setup_handler)
    guild_id = os.getenv("DISCORD_GUILD_ID")
    if guild_id:
        wrapped = app_commands.guilds(discord.Object(id=int(guild_id)))(wrapped)

    bot.tree.command(
        name="daily-setup",
        description="Posta o painel de Daily neste canal (somente administradores).",
    )(wrapped)
