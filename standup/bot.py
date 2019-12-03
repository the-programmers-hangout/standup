"""Module for hosting bot related functionality."""

# pylint: disable=missing-function-docstring

import asyncio
from datetime import datetime, timezone
from typing import List

import discord
from discord.ext import commands
from standup.post import message_is_formatted
from standup.persist import Post, Room


STANDUP_DM_HELP = """Please format your standup correctly, here is a template example: ```
Yesterday I: [...]
Today I will: [...]
Potential hard problems: [...]
```\n
"""
BOT = commands.Bot(command_prefix=commands.when_mentioned)


@BOT.event
async def on_command_completion(ctx: commands.Context):
    await ctx.message.add_reaction("✅")


@BOT.event
async def on_command_error(ctx: commands.Context, exception):
    await ctx.message.add_reaction("❌")

    if isinstance(exception, commands.MissingPermissions):
        await ctx.send(
            f"```\nFailed: missing permissions `{', '.join(exception.missing_perms)}`.```"
        )
    else:
        exception_str = str(exception)
        if not exception_str:
            return

        await ctx.send(f"```\n{exception_str}```")


@BOT.event
async def on_message(msg: discord.Message):
    await BOT.process_commands(msg)

    related_room = Room.select().where(Room.channel_id == msg.channel.id).first()
    if not related_room:
        return

    if not message_is_formatted(msg.content):
        await msg.delete()
        await msg.author.send(STANDUP_DM_HELP)
        return

    new_post = Post.create(
        channel_id=msg.channel.id,
        user_id=msg.author.id,
        role_ids=related_room.role_ids,
        timestamp=datetime.now(tz=timezone.utc),
    )

    await _process_role_assignment(new_post)


async def _process_role_assignment(post: Post):
    guild = BOT.get_channel(post.channel_id).guild
    member = guild.get_member(post.user_id)
    roles_to_add = [guild.get_role(id) for id in post.role_ids]
    await member.add_roles(*roles_to_add)


@BOT.group(name="rooms")
async def rooms_group(ctx: commands.Context):
    """Manage standup rooms."""

    if not ctx.invoked_subcommand:
        await ctx.send_help(rooms_group)


@rooms_group.command(name="add")
@commands.has_permissions(administrator=True)
async def rooms_add(ctx: commands.Context, channel_id: int):
    """Declares a discord channel as a standup room."""

    conflicting_room = Room.select().where(Room.channel_id == channel_id).first()
    if conflicting_room:
        await ctx.send(f"```Failed: channel '{channel_id}' already is a room.```")
        raise commands.CommandError()

    Room.create(channel_id=channel_id, role_ids=set())


@rooms_group.command(name="remove")
@commands.has_permissions(administrator=True)
async def rooms_remove(_, channel_id: int):
    """Removes a discord channel from the list of functional standup rooms."""

    Room.delete().where(Room.channel_id == channel_id).execute()


@rooms_group.command(name="list")
@commands.has_permissions(administrator=True)
async def rooms_list(ctx: commands.Context):
    """Lists all created standup rooms along with their assigned roles."""

    rooms = Room.select()
    formatted = (r.format_for_listing() for r in rooms)
    numbered = (f"{i}: {string}" for i, string in enumerate(formatted, 1))
    joined = "\n".join(numbered)

    await ctx.send(f"```\n{joined}```")


@rooms_group.command(name="config")
@commands.has_permissions(administrator=True)
async def rooms_config(ctx: commands.Context, room: int, key: str, value: str):
    """
    Configures a standup room's key-value properties.

    Keys:
    - 'roles': Accepts a comma separated list of role IDs. Use empty quotes to
      specify an empty list.
    - 'cooldown': Accepts an integer value representing seconds. (default: 86400)
    """

    target_room = Room.select().where(Room.channel_id == room).first()
    if not target_room:
        ctx.send(f"```\nFailed: room '{room}' does not exist.```")
        raise commands.CommandError()

    if key == "roles":
        snowflakes = _parse_snowflake_csv(value)
        role_ids = set(id for id in snowflakes if ctx.guild.get_role(id))
        target_room.role_ids = Room.role_ids.db_value(role_ids)
        target_room.save()
    elif key == "cooldown":
        Room.update(cooldown=int(value)).where(Room.channel_id == room).execute()


def _parse_snowflake_csv(string: str) -> List[int]:
    return [int(s) for s in string.split(",") if s]


GITHUB_URL = "https://www.github.com/skippi/standup"


@BOT.command(aliases=["about"])
async def info(ctx: commands.Context):
    """Displays information about the standup bot."""

    embed = discord.Embed()
    embed.colour = discord.Colour(0x43B581)
    embed.description = (
        'A discord bot for conducting daily stand-ups in "The Programming Hangout".'
    )
    embed.set_author(name="Info", url=GITHUB_URL, icon_url=str(BOT.user.avatar_url))
    embed.set_thumbnail(url=str(BOT.user.avatar_url))
    embed.add_field(name="GitHub", value=f"[skippi/standup]({GITHUB_URL})")
    embed.add_field(
        name="Framework", value="[discord.py](https://github.com/Rapptz/discord.py)"
    )

    await ctx.send(embed=embed)


async def _prune_expired_posts_task():
    await BOT.wait_until_ready()

    while not BOT.is_closed():
        await asyncio.sleep(60)

        expired_posts = Post.select_expired_posts(datetime.now(tz=timezone.utc))
        if len(expired_posts) == 0:
            continue

        for post in expired_posts:
            await _process_role_removal(post)
            post.delete_instance()


async def _process_role_removal(post: Post):
    guild = BOT.get_channel(post.channel_id).guild
    member = guild.get_member(post.user_id)
    roles_to_remove = [guild.get_role(id) for id in post.role_ids]
    await member.remove_roles(*roles_to_remove)


BOT.loop.create_task(_prune_expired_posts_task())
