# bot.py

import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import json
import random
import os
import pytz
import aiohttp
from discord import Attachment
from datetime import datetime, timedelta
from dotenv import load_dotenv
from sheets import get_tile_data, get_max_tile

load_dotenv()

# Global data dicts and variables
pending_submissions = {}  # {(guild_id:int, user_id:int): {tile, task, target, drop_rate, message_id, channel_id}}

# --- CONFIGURATION ---
SNL_ROLE = "SNL"
SNL_HOST_ROLE = "SNL Host"
SUBMISSION_CHANNEL = "snl-submissions"
ADMIN_CHANNEL = "snl-admin"
CHAT_CHANNEL = "snl-chat"
SNL_COMMANDS_CHANNEL = "snl-commands"
TIMEZONE_OFFSET = 10  # Melbourne is UTC+10 or UTC+11 with daylight saving
WEB_APP_URL="https://script.google.com/macros/s/AKfycbxCnpUEMVkujBNDBbcaD14Nf57R7HrvPp9uR0_d36U0s9oeGIV96wsq9GanZrT6-9ZO/exec"


# --- INIT BOT ---
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)

data_file = "data.json"

# --- LOAD / SAVE ---
def load_data():
    if os.path.exists(data_file):
        with open(data_file, "r") as f:
            return json.load(f)
    return {"positions": {}, "rolls": {}, "approvals": {}, "podium": {}}

def save_data():
    with open(data_file, "w") as f:
        json.dump(data, f, indent=4)

data = load_data()

# --- TIME HELPERS ---
def seconds_until(hour: int):
    """Returns seconds until the next occurrence of the given hour in Melbourne time."""
    now = datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()

def format_next_grant():
    """Returns a string for the next roll grant time in Melbourne time."""
    now = datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)
    if now.hour < 12:
        next_time = now.replace(hour=12, minute=0, second=0, microsecond=0)
    elif now.hour < 24:
        next_time = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    else:
        next_time = now.replace(hour=12, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return next_time.strftime("%I:%M %p").lstrip("0")

def next_midnight_melbourne():
    """Returns a timedelta object representing the time until the next midnight in Melbourne time."""
    MELBOURNE_TZ = pytz.timezone("Australia/Melbourne")
    now = datetime.now(MELBOURNE_TZ)
    if now.hour < 12:
        next_midnight = now.replace(hour=12, minute=0, second=0, microsecond=0)
    else:
        next_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    delta = next_midnight - now
    return delta

# --- BACKGROUND TASK ---
@tasks.loop(hours=12)
async def grant_daily_rolls():
    await bot.wait_until_ready()
    now = datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)
    current_hour = now.hour

    if current_hour == 0:
        announcement = f"🌙 **Midnight rolls have been granted!** Everyone has +1 roll. ⏭️ Next rolls: **{format_next_grant()}**"
    elif current_hour == 12:
        announcement = f"☀️ **Midday rolls have been granted!** Everyone has +1 roll. ⏭️ Next rolls: **{format_next_grant()}**"
    else:
        announcement = f"🎲 **Daily rolls have been granted!** Everyone has +1 roll. ⏭️ Next rolls: **{format_next_grant()}**"

    for guild in bot.guilds:
        role = discord.utils.get(guild.roles, name=SNL_ROLE)
        if not role:
            continue
        guild_id = str(guild.id)
        data.setdefault("rolls", {}).setdefault(guild_id, {})
        data.setdefault("approvals", {}).setdefault(guild_id, {})

        for member in role.members:
            user_id = str(member.id)
            data["rolls"][guild_id].setdefault(user_id, 0)
            data["rolls"][guild_id][user_id] += 1
            data["approvals"][guild_id].setdefault(user_id, True)

        # Post announcement in SNL-chat if it exists
        channel = discord.utils.get(guild.text_channels, name="snl-chat")
        if channel:
            await channel.send(announcement)

    save_data()
    print("Daily rolls granted.")

@grant_daily_rolls.before_loop
async def before_daily_rolls():
    """Aligns the first run to the next closest midday or midnight Melbourne time."""
    now = datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)

    # Determine next run time
    if now.hour < 12:
        sleep_seconds = seconds_until(12)
    elif now.hour < 24:
        sleep_seconds = seconds_until(0)
    else:
        sleep_seconds = seconds_until(12)

    print(f"Sleeping for {sleep_seconds} seconds until the next roll grant.")
    await asyncio.sleep(sleep_seconds)


# --- HELPER FUNCTIONS ---
def is_snl_commands_channel(interaction: discord.Interaction) -> bool:
    return interaction.channel.name == SNL_COMMANDS_CHANNEL

def can_use_command(interaction: discord.Interaction, role_name: str) -> bool:
    return discord.utils.get(interaction.user.roles, name=role_name) is not None

def format_tile_message(user: discord.Member, tile_data: dict, rolled: int = None, from_tile: int = None, to_tile: int = None, snake_ladder: str = ""):
    if not tile_data:
        content = f"{user.mention}, there was an issue fetching tile data."
        embed = discord.Embed(title="Tile Data Missing", color=discord.Color.red())
        return content, embed

    embed = discord.Embed(title=f"Tile {tile_data['Tile']}", color=discord.Color.gold())
    embed.add_field(name="Target", value=tile_data["Target"], inline=True)
    embed.add_field(name="Task", value=tile_data["Task"], inline=True)
    embed.add_field(name="Drop Rate", value=tile_data["Drop Rate"], inline=True)

    image_url = tile_data.get("Image")

    if image_url and isinstance(image_url, str) and image_url.startswith("http"):
        embed.set_image(url=image_url)

    if rolled is not None:
        if snake_ladder == "ladder":
            content = f"Hurray! {user.mention} rolled a {rolled} and landed on a ladder!\n"
        elif snake_ladder == "snake":
            content = f"Oh no! {user.mention} rolled a {rolled} and slid down a snake. Sit!\n"
        else:
            content = f"{user.mention} has rolled a {rolled}!\n"
        content += f"You moved from Tile {from_tile} to Tile {to_tile}."
    else:
        content = f"{user.mention}, your current position is Tile {tile_data['Tile']}."

    return content, embed

# /roll
@bot.tree.command(name="roll", description="Roll the dice (1–6) to move along the board")
async def roll(interaction: discord.Interaction):
    if not is_snl_commands_channel(interaction):
        # This message will be visible only to the user.
        await interaction.response.send_message(
            f"You can only use this command in the #{SNL_COMMANDS_CHANNEL} channel.",
            ephemeral=False  # Only visible to the user
        )
        return

    # Defer response so the bot doesn't timeout while processing.
    await interaction.response.defer(ephemeral=False)

    user_id = str(interaction.user.id)
    guild_id = str(interaction.guild.id)

    # Ensure data structures exist
    data.setdefault("approvals", {}).setdefault(guild_id, {})
    data.setdefault("rolls", {}).setdefault(guild_id, {})
    data.setdefault("positions", {}).setdefault(guild_id, {})
    if "podium" not in data or not isinstance(data["podium"], dict):
        data["podium"] = {}
    if guild_id not in data["podium"]:
        data["podium"][guild_id] = []

    # Default values for user if missing
    data["approvals"][guild_id].setdefault(user_id, True)
    data["rolls"][guild_id].setdefault(user_id, 0)
    data["positions"][guild_id].setdefault(user_id, 1)

    # Check if the user has finished the game and is in the podium
    if user_id in data["podium"][guild_id]:
        podium_position = data["podium"][guild_id].index(user_id) + 1  # 1-based index
        # Send message **only to the user** (ephemeral)
        await interaction.followup.send(
            f"You have finished the current game in position #{podium_position}. Please wait for the next game to roll again.",
            ephemeral=True  # Only visible to the user
        )
        return

    if not data["approvals"][guild_id][user_id]:
        await interaction.followup.send(
            "You must wait until your last submission is approved before rolling again.",
            ephemeral=True  # Only visible to the user
        )
        return

    if data["rolls"][guild_id][user_id] <= 0:
        delta = next_midnight_melbourne()

        now = datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)
        if now.hour < 12:
            next_time = now.replace(hour=12, minute=0, second=0, microsecond=0)
        elif now.hour < 24:
            next_time = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        else:
            next_time = now.replace(hour=12, minute=0, second=0, microsecond=0) + timedelta(days=1)
        next_grant_str = next_time.strftime("%I:%M %p").lstrip("0")

        # Ephemeral response, **only the user will see this**
        await interaction.followup.send(
            f"You have no rolls left. ⏭️ Next auto-grant: {str(delta).split('.')[0]}.",
            ephemeral=True  # **Only visible to the user**
        )
        return

    current = data["positions"][guild_id][user_id]
    roll_value = random.randint(1, 6)
    max_tile = get_max_tile()
    next_tile = current + roll_value

    # Bounce-back logic
    if next_tile > max_tile:
        overflow = next_tile - max_tile
        next_tile = max_tile - overflow

    # Check for snake or ladder
    tile_data = get_tile_data(next_tile)
    if tile_data and tile_data["Type"] in ["ladder", "snake"]:
        from_tile = next_tile
        next_tile = tile_data["End Tile"]
        snake_ladder = tile_data["Type"]
    else:
        from_tile = current
        snake_ladder = ""

    data["positions"][guild_id][user_id] = next_tile
    data["rolls"][guild_id][user_id] -= 1
    data["approvals"][guild_id][user_id] = False  # Lock until host approves
    save_data()

    # Special handling if the player reaches tile 100 (or max_tile)
    if next_tile == max_tile:
        if user_id not in data["podium"][guild_id]:
            data["podium"][guild_id].append(user_id)
            save_data()
        
        # Prevent them from rolling again after finishing
        data["rolls"][guild_id][user_id] = 0  # Set rolls to 0 once they finish
        data["approvals"][guild_id][user_id] = True  # Reset approval to True for the next game
        save_data()

        # Custom message for finishing the game (ephemeral so only the user sees it)
        podium_position = len(data["podium"][guild_id])  # Podium position is based on their finishing order
        await interaction.followup.send(
            f"🎉 You have finished the game! You finished the board in position #{podium_position}!",
            ephemeral=True  # **This ensures only the player sees it**
        )
    else:
        final_tile_data = get_tile_data(next_tile)
        if final_tile_data is None:
            # Fallback in case get_tile_data fails, to prevent errors
            content = f"{interaction.user.mention}, you moved to Tile {next_tile}."
            embed = None
        else:
            content, embed = format_tile_message(interaction.user, final_tile_data, rolled=roll_value, from_tile=current, to_tile=next_tile, snake_ladder=snake_ladder)

        # Send the message **only visible to the player who triggered the command**
        await interaction.followup.send(content, embed=embed, ephemeral=True)  # **Ephemeral so only they see it**





# /position
@bot.tree.command(name="position", description="Check your position on the board")
async def position(interaction: discord.Interaction):
    if not is_snl_commands_channel(interaction):
        await interaction.response.send_message(
            f"You can only use this command in the #{SNL_COMMANDS_CHANNEL} channel.",
            ephemeral=True
        )
        return

    user_id = str(interaction.user.id)
    guild_id = str(interaction.guild.id)

    data.setdefault("positions", {}).setdefault(guild_id, {}).setdefault(user_id, 1)
    data.setdefault("approvals", {}).setdefault(guild_id, {}).setdefault(user_id, False)
    data.setdefault("rolls", {}).setdefault(guild_id, {}).setdefault(user_id, 0)
    save_data()

    # Check if the user's submission is approved
    if data["approvals"][guild_id][user_id]:
        # No active tile (submission approved)
        if data["rolls"][guild_id][user_id] <= 0:
            # No rolls left, show when the next roll will be available
            delta = next_midnight_melbourne()
            next_grant_str = format_next_grant()
            await interaction.response.send_message(
                f"You have no active tile. You have no rolls left. ⏭️ Next auto-grant: {str(delta).split('.')[0]}.",
                ephemeral=True
            )
        else:
            # Rolls available, prompt to use /roll
            await interaction.response.send_message(
                f"You have no active tile. Use `/roll` to get your next tile assignment.",
                ephemeral=True
            )
    else:
        # Submission not approved, show the current tile
        current_tile = data["positions"][guild_id][user_id]
        if current_tile == 0:
            await interaction.response.send_message("You are at the start, use /roll to start the game.", ephemeral=True)
            return

        tile_data = get_tile_data(current_tile)
        data.setdefault("podium", {}).setdefault(guild_id, [])
        if current_tile == get_max_tile():
            if user_id in data["podium"][guild_id]:
                await interaction.response.send_message(f"You have already finished this round, your podium position is: #{data['podium'][guild_id].index(user_id)+1}")
                return

        content, embed = format_tile_message(interaction.user, tile_data)
        await interaction.response.send_message(content, embed=embed)

# /checkrolls
@bot.tree.command(name="checkrolls", description="Check how many rolls and when cooldown ends")
async def checkrolls(interaction: discord.Interaction):
    if not is_snl_commands_channel(interaction):
        await interaction.response.send_message(
            f"You can only use this command in the #{SNL_COMMANDS_CHANNEL} channel.",
            ephemeral=True
        )
        return
    
    user_id = str(interaction.user.id)
    guild_id = str(interaction.guild.id)

    data.setdefault("rolls", {}).setdefault(guild_id, {}).setdefault(user_id, 0)

    # Get the time delta until the next midnight in Melbourne time
    delta = next_midnight_melbourne()

    # Get next grant time in Melbourne time
    now = datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)
    if now.hour < 12:
        next_time = now.replace(hour=12, minute=0, second=0, microsecond=0)
    elif now.hour < 24:
        next_time = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    else:
        next_time = now.replace(hour=12, minute=0, second=0, microsecond=0) + timedelta(days=1)
    
    next_grant_str = next_time.strftime("%I:%M %p").lstrip("0")

    # Send the message with the current number of rolls and next grant time
    await interaction.response.send_message(
        f"You have {data['rolls'][guild_id][user_id]} roll(s) left. "
        f"⏭️ Next auto-grant: {str(delta).split('.')[0]}.",
        ephemeral=True
    )


# /submit
@bot.tree.command(name="submit", description="Submit your tile (image required)")
@app_commands.describe(image="Upload an image with your submission")
async def submit(interaction: discord.Interaction, image: Attachment):
    if interaction.channel.name != SNL_COMMANDS_CHANNEL:
        await interaction.response.send_message(f"You can only use this command in #{SNL_COMMANDS_CHANNEL}.", ephemeral=True)
        return

    if not image:
        await interaction.response.send_message("You must attach an image with your submission!", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    user_id = str(interaction.user.id)
    guild_id = str(interaction.guild.id)

    # Set approval to pending
    data.setdefault("approvals", {}).setdefault(guild_id, {})[user_id] = False
    save_data()

    # Get user tile position, default to 1 if missing
    tile_number = data.get("positions", {}).get(guild_id, {}).get(user_id, 1)
    tile_data = get_tile_data(tile_number)

    # Prepare submission message text
    if tile_data:
        tile_num = tile_data.get("Tile", tile_number)
        task = tile_data.get("Task", "Unknown Task")
        target = tile_data.get("Target", "Unknown Target")
        drop_rate = tile_data.get("Drop Rate", "Unknown Drop Rate")
        submission_message = (
            f"**{interaction.user.display_name}** has submitted **Tile {tile_num}**, "
            f"**{task}** from **{target}** ({drop_rate})."
        )
    else:
        submission_message = f"{interaction.user.display_name} has submitted their task."

    # Get the snl-submissions channel
    submissions_channel = discord.utils.get(interaction.guild.text_channels, name=SUBMISSION_CHANNEL)
    if not submissions_channel:
        await interaction.followup.send(f"Submission channel #{SUBMISSION_CHANNEL} not found!", ephemeral=True)
        return

    # Send the submission image message in snl-submissions channel
    msg = await submissions_channel.send(
        submission_message,
        file=await image.to_file()
    )
    await msg.add_reaction("✅")

    # Store the submission info so approval links to this exact message
    pending_submissions[(int(guild_id), int(user_id))] = {
        "tile": tile_num if tile_data else None,
        "task": task if tile_data else None,
        "target": target if tile_data else None,
        "drop_rate": drop_rate if tile_data else None,
        "message_id": msg.id,
        "channel_id": msg.channel.id
    }

    # Send ephemeral confirmation to user
    await interaction.followup.send("Submission received! A host will approve it shortly.", ephemeral=True)

    # Post outstanding approvals to #snl-admin
    admin_channel = discord.utils.get(interaction.guild.text_channels, name=ADMIN_CHANNEL)
    if admin_channel:
        pending = data["approvals"].get(guild_id, {})
        user_submissions = {}

        # Only include users with pending approval who have a stored submission message
        for (g_id, u_id), info in pending_submissions.items():
            if str(g_id) == guild_id and pending.get(str(u_id)) is False:
                user = interaction.guild.get_member(u_id)
                if user:
                    jump_url = f"https://discord.com/channels/{guild_id}/{info['channel_id']}/{info['message_id']}"
                    user_submissions[u_id] = (user, jump_url, info)

        if user_submissions:
            embed = discord.Embed(
                title="🕓 Outstanding Approvals",
                description="\n".join(
                    f"🔸 {user.mention} — Tile {info['tile']}, {info['task']} from {info['target']} ({info['drop_rate']}) — [Jump to Submission]({url})"
                    for _, (user, url, info) in user_submissions.items()
                ),
                color=discord.Color.orange()
            )
            await admin_channel.send(embed=embed)

# /addroll
@bot.tree.command(name="addroll", description="Add roll(s) to a player (SNL Host Only)")
@app_commands.describe(user="Player to add rolls to", amount="Number of rolls")
async def addroll(interaction: discord.Interaction, user: discord.Member, amount: int):
    if not is_snl_commands_channel(interaction):
        await interaction.response.send_message(
            f"You can only use this command in the #{SNL_COMMANDS_CHANNEL} channel.",
            ephemeral=True
        )
        return

    if not can_use_command(interaction, SNL_HOST_ROLE):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    user_id = str(user.id)
    guild_id = str(interaction.guild.id)

    data.setdefault("rolls", {}).setdefault(guild_id, {}).setdefault(user_id, 0)
    data["rolls"][guild_id][user_id] += amount
    save_data()

    await interaction.response.send_message(f"{amount} roll(s) added to {user.mention}.")

# /removeroll
@bot.tree.command(name="removeroll", description="Remove roll(s) from a player (SNL Host Only)")
@app_commands.describe(user="Player to remove rolls from", amount="Number of rolls")
async def removeroll(interaction: discord.Interaction, user: discord.Member, amount: int):
    if not is_snl_commands_channel(interaction):
        await interaction.response.send_message(
            f"You can only use this command in the #{SNL_COMMANDS_CHANNEL} channel.",
            ephemeral=True
        )
        return

    if not can_use_command(interaction, SNL_HOST_ROLE):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    user_id = str(user.id)
    guild_id = str(interaction.guild.id)

    data.setdefault("rolls", {}).setdefault(guild_id, {}).setdefault(user_id, 0)
    data["rolls"][guild_id][user_id] = max(0, data["rolls"][guild_id][user_id] - amount)
    save_data()

    await interaction.response.send_message(f"{amount} roll(s) removed from {user.mention}.")

# /setpos
@bot.tree.command(name="setpos", description="Change user's tile position (SNL Host Only)")
@app_commands.describe(user="Player to move", tile="New tile number")
async def setpos(interaction: discord.Interaction, user: discord.Member, tile: int):
    if not is_snl_commands_channel(interaction):
        await interaction.response.send_message(
            f"You can only use this command in the #{SNL_COMMANDS_CHANNEL} channel.",
            ephemeral=True
        )
        return

    if not can_use_command(interaction, SNL_HOST_ROLE):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    user_id = str(user.id)
    guild_id = str(interaction.guild.id)

    data.setdefault("positions", {}).setdefault(guild_id, {}).setdefault(user_id, 1)
    old_tile = data["positions"][guild_id][user_id]
    data["positions"][guild_id][user_id] = tile
    save_data()

    await interaction.response.send_message(f"{user.mention} has been moved from Tile {old_tile} to Tile {tile} by {interaction.user.mention}.")

# /board
@bot.tree.command(name="board", description="View the board")
async def board(interaction: discord.Interaction):
    if not is_snl_commands_channel(interaction):
        await interaction.response.send_message(
            f"You can only use this command in the #{SNL_COMMANDS_CHANNEL} channel.",
            ephemeral=True
        )
        return
    if os.path.exists("board.jpg"):
        await interaction.response.send_message(file=discord.File("board.jpg"))
    else:
        await interaction.response.send_message("Board image not found.", ephemeral=True)


# /leaderboard
@bot.tree.command(name="leaderboard", description="Display the leaderboard")
async def leaderboard(interaction: discord.Interaction):
    if not is_snl_commands_channel(interaction):
        await interaction.response.send_message(
            f"You can only use this command in the #{SNL_COMMANDS_CHANNEL} channel.",
            ephemeral=True
        )
        return

    await interaction.response.defer()  # ✅ Minimal fix — prevents Unknown interaction error

    guild_id = str(interaction.guild.id)
    data.setdefault("positions", {}).setdefault(guild_id, {})
    data.setdefault("rolls", {}).setdefault(guild_id, {})

    rows = []
    for user_id, tile in data["positions"][guild_id].items():
        if tile == 0:  # Skip players at tile 0
            continue
        user = interaction.guild.get_member(int(user_id))
        if not user:
            continue
        tile_data = get_tile_data(tile)
        if tile_data is None:
            continue
        rolls_left = data["rolls"][guild_id].get(user_id, 0)
        rows.append((tile, user.display_name, tile_data["Target"], tile_data["Task"], rolls_left))

    # Sort by tile descending (highest first)
    rows.sort(key=lambda x: x[0], reverse=True)

    # Build leaderboard lines with emojis and plain text
    lines = []
    for idx, (tile, name, target, task, rolls_left) in enumerate(rows, start=1):
        # Truncate long text
        target = (target[:20] + "...") if len(target) > 23 else target
        task = (task[:30] + "...") if len(task) > 33 else task

        # Emoji for position
        if idx == 1:
            position_emoji = "🥇"
        elif idx == 2:
            position_emoji = "🥈"
        elif idx == 3:
            position_emoji = "🥉"
        else:
            position_emoji = f"🔢 {idx}."

        line = (
            f"{position_emoji} **{name}** — "
            f"**Tile:** {tile} | "
            f"**Target:** {target} | "
            f"**Task:** {task} | "
            f"**Rolls Left:** {rolls_left}"
        )
        lines.append(line)

    embed = discord.Embed(
        title="🎲 Snakes and Ladders Leaderboard",
        description="\n".join(lines) if lines else "*No players on the board yet.*",
        color=discord.Color.gold()
    )

    await interaction.followup.send(embed=embed)  # ✅ Use followup after deferring





# /podium
@bot.tree.command(name="podium", description="Show users who finished the board")
async def podium(interaction: discord.Interaction):
    if not is_snl_commands_channel(interaction):
        await interaction.response.send_message(
            f"You can only use this command in the #{SNL_COMMANDS_CHANNEL} channel.",
            ephemeral=True
        )
        return
    guild_id = str(interaction.guild.id)
    data.setdefault("podium", {}).setdefault(guild_id, [])

    if not data["podium"][guild_id]:
        await interaction.response.send_message("No players have reached the end yet.")
        return

    message = "**🏆 Podium Placements 🏆**\n"
    for i, user_id in enumerate(data["podium"][guild_id], start=1):
        user = interaction.guild.get_member(int(user_id))
        name = user.mention if user else f"<@{user_id}>"
        message += f"{i}. {name}\n"

    await interaction.response.send_message(message)

# /reset
@bot.tree.command(name="reset", description="Reset game (SNL Host Only)")
async def reset(interaction: discord.Interaction):
    if not is_snl_commands_channel(interaction):
        await interaction.response.send_message(
            f"You can only use this command in the #{SNL_COMMANDS_CHANNEL} channel.",
            ephemeral=True
        )
        return

    if not can_use_command(interaction, SNL_HOST_ROLE):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    guild_id = str(interaction.guild.id)

    async def confirm_reset(interaction_to_use):
        # Reset data
        data["positions"][guild_id] = {}
        data["rolls"][guild_id] = {}
        data["approvals"][guild_id] = {}
        data["podium"][guild_id] = []

        for member in interaction_to_use.guild.members:
            if not member.bot:
                user_id = str(member.id)
                data["positions"][guild_id][user_id] = 0  # start on Tile 0
                data["rolls"][guild_id][user_id] = 1
                data["approvals"][guild_id][user_id] = True

        save_data()

        # Send message tagging SNL role in #snl-chat
        snl_role = discord.utils.get(interaction_to_use.guild.roles, name=SNL_ROLE)
        snl_chat_channel = discord.utils.get(interaction_to_use.guild.text_channels, name="snl-chat")
        if snl_role and snl_chat_channel:
            await snl_chat_channel.send(f"{snl_role.mention}, the game has been reset!")

        await interaction_to_use.followup.send("Game has been reset. Everyone is back to Tile 0 with 1 roll.", ephemeral=True)

    view = discord.ui.View(timeout=30)

    class Confirm(discord.ui.Button):
        def __init__(self):
            super().__init__(style=discord.ButtonStyle.danger, label="Reset Game")

        async def callback(self, interaction2: discord.Interaction):
            await interaction2.response.defer()
            await confirm_reset(interaction2)

    view.add_item(Confirm())

    await interaction.response.send_message(
        "Are you sure you want to reset the game and everyone’s progress?",
        view=view,
        ephemeral=True
    )

# ✅ Reaction handler for submission approval
@bot.event
async def on_raw_reaction_add(payload):
    if str(payload.emoji) != "✅":
        return

    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return

    member = guild.get_member(payload.user_id)
    if member is None or not any(role.name == SNL_HOST_ROLE for role in member.roles):
        return

    channel = guild.get_channel(payload.channel_id)
    if channel is None or channel.name != SUBMISSION_CHANNEL:
        return

    try:
        message = await channel.fetch_message(payload.message_id)
    except discord.NotFound:
        return

    if not message.attachments or len(message.mentions) != 1:
        return

    user = message.mentions[0]
    guild_id = str(guild.id)
    user_id = str(user.id)

    if data["approvals"].get(guild_id, {}).get(user_id) is not False:
        return  # Already approved

    # Mark as approved
    data["approvals"][guild_id][user_id] = True
    save_data()

    # Check if they can roll now
    rolls = data["rolls"].get(guild_id, {}).get(user_id, 0)
    can_roll = rolls > 0

    # Calculate next roll time
    now = datetime.now(pytz.timezone("Australia/Melbourne"))
    next_reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    time_until_reset = next_reset - now
    formatted_time = str(time_until_reset).split(".")[0]  # Remove microseconds

    # Compose message
    if can_roll:
        msg = f"{user.mention}'s submission has been approved by {member.mention}. You are now free to roll again."
    else:
        msg = f"{user.mention}'s submission has been approved by {member.mention}. You need to wait `{formatted_time}` until you can roll again."

    # Post to snl-chat
    chat_channel = discord.utils.get(guild.text_channels, name=SUBMISSION_CHANNEL)
    if chat_channel:
        await chat_channel.send(msg)

# Reactions

@bot.event
async def on_raw_reaction_add(payload):
    if str(payload.emoji) != "✅":
        return

    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return

    member = guild.get_member(payload.user_id)
    if member is None or not any(role.name == SNL_HOST_ROLE for role in member.roles):
        return

    channel = guild.get_channel(payload.channel_id)
    if channel is None or channel.name != SUBMISSION_CHANNEL:
        return

    try:
        message = await channel.fetch_message(payload.message_id)
    except discord.NotFound:
        return

    # Find which user this submission belongs to by matching message ID in pending_submissions
    approved_user_id = None
    for (g_id, u_id), info in pending_submissions.items():
        if g_id == guild.id and info["message_id"] == message.id:
            approved_user_id = u_id
            break

    if approved_user_id is None:
        return

    guild_id = str(guild.id)
    user_id = str(approved_user_id)

    if data["approvals"].get(guild_id, {}).get(user_id) is not False:
        return  # Already approved

    # Mark as approved
    data["approvals"][guild_id][user_id] = True
    save_data()

    # Remove from pending_submissions since approved
    pending_submissions.pop((guild.id, approved_user_id), None)

    # Check if they can roll now
    rolls = data["rolls"].get(guild_id, {}).get(user_id, 0)
    can_roll = rolls > 0

    # Calculate next roll time
    now = datetime.now(pytz.timezone("Australia/Melbourne"))
    next_reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    time_until_reset = next_reset - now
    formatted_time = str(time_until_reset).split(".")[0]  # Remove microseconds

    # Compose message
    if can_roll:
        msg = f"<@{user_id}>'s submission has been approved by {member.mention}. You are now free to roll again."
    else:
        msg = f"<@{user_id}>'s submission has been approved by {member.mention}. You need to wait `{formatted_time}` until you can roll again."

    # Post to snl-submissions
    chat_channel = discord.utils.get(guild.text_channels, name=SUBMISSION_CHANNEL)
    if chat_channel:
        await chat_channel.send(msg)

    # Send updated outstanding approvals embed to snl-admin
    admin_channel = discord.utils.get(guild.text_channels, name=ADMIN_CHANNEL)
    if admin_channel:
        pending = data["approvals"].get(guild_id, {})
        user_submissions = {}

        for (g_id, u_id), info in pending_submissions.items():
            if str(g_id) == guild_id and pending.get(str(u_id)) is False:
                user = guild.get_member(u_id)
                if user:
                    jump_url = f"https://discord.com/channels/{guild_id}/{info['channel_id']}/{info['message_id']}"
                    user_submissions[u_id] = (user, jump_url, info)

        if user_submissions:
            embed = discord.Embed(
                title="📋 Updated Outstanding Approvals",
                description="\n".join(
                    f"🔸 {user.mention} — Tile {info['tile']}, {info['task']} from {info['target']} ({info['drop_rate']}) — [Jump to Submission]({url})"
                    for _, (user, url, info) in user_submissions.items()
                ),
                color=discord.Color.orange()
            )
        else:
            embed = discord.Embed(
                title="✅ All submissions have been approved!",
                color=discord.Color.green()
            )
        await admin_channel.send(embed=embed)
# Start the bot
bot.run(os.getenv("DISCORD_TOKEN"))
