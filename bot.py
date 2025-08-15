import os
import discord
from discord.ext import commands
from discord import app_commands, Embed, ButtonStyle
from discord.ui import View, Button
from flask import Flask, request
import threading
import datetime
import json

TOKEN = os.getenv("TOKEN")
DEBUG_PASSWORD = "soulbinder0123"
debug_mode_users = set()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree

# -------------------- Flask API for executor --------------------
app = Flask("executor_api")
executor_data = {}  # Stores last scans per server

@app.route("/api/data", methods=["POST"])
def receive_data():
    try:
        data = request.json
        if data:
            server_id = data.get("jobId", "unknown")
            executor_data[server_id] = data
            print(f"[Executor API] Received data from server {server_id}")
        return {"status": "ok"}, 200
    except Exception as e:
        print("[Executor API] Failed:", e)
        return {"status": "error"}, 500

def run_flask():
    app.run(host="0.0.0.0", port=8080)

threading.Thread(target=run_flask).start()

# -------------------- Bot Ready --------------------
@bot.event
async def on_ready():
    print(f"[Bot] Logged in as {bot.user}")
    try:
        await tree.sync()
        print("[Bot] Commands synced")
    except Exception as e:
        print("[Bot] Sync failed:", e)

# -------------------- Basic Commands --------------------
@tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"Pong! Latency: {round(bot.latency*1000)}ms")

@tree.command(name="status", description="Bot status and last updates")
async def status(interaction: discord.Interaction):
    embed = Embed(title="Soulbinder Status", color=discord.Color.dark_blue())
    embed.add_field(name="Tracked Servers", value=str(len(executor_data)))
    embed.add_field(name="Last Update", value=datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"))
    embed.add_field(name="Debug Mode Users", value=", ".join([str(u) for u in debug_mode_users]) or "None")
    await interaction.response.send_message(embed=embed)

# -------------------- Login Command --------------------
@tree.command(name="login", description="Enter debug mode with password")
@app_commands.describe(password="Enter the debug password")
async def login(interaction: discord.Interaction, password: str):
    if password == DEBUG_PASSWORD:
        debug_mode_users.add(interaction.user.id)
        await interaction.response.send_message("✅ Debug mode activated for you.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Incorrect password.", ephemeral=True)

# -------------------- Debug Command --------------------
@tree.command(name="debug", description="View raw executor data (debug mode only)")
@app_commands.describe(server_id="Server Job ID to view raw data")
async def debug(interaction: discord.Interaction, server_id: str):
    if interaction.user.id not in debug_mode_users:
        await interaction.response.send_message("❌ You must be in debug mode to use this command. Use `/login` with the password.", ephemeral=True)
        return

    data = executor_data.get(server_id)
    if not data:
        await interaction.response.send_message(f"No data for server {server_id}", ephemeral=True)
        return

    json_data = json.dumps(data, indent=2)
    if len(json_data) > 1900:
        json_data = json_data[:1900] + "\n... (truncated)"
    await interaction.response.send_message(f"```json\n{json_data}\n```", ephemeral=True)

# -------------------- Server List with Pagination --------------------
class ServerListView(View):
    def __init__(self, servers):
        super().__init__(timeout=None)
        self.servers = list(servers.items())
        self.index = 0
        self.max_per_page = 5
        self.prev_button = Button(label="⬅️", style=ButtonStyle.blurple)
        self.next_button = Button(label="➡️", style=ButtonStyle.blurple)
        self.prev_button.callback = self.prev_page
        self.next_button.callback = self.next_page
        self.add_item(self.prev_button)
        self.add_item(self.next_button)

    async def prev_page(self, interaction: discord.Interaction):
        self.index = max(self.index - self.max_per_page, 0)
        await interaction.response.edit_message(embed=self.get_embed())

    async def next_page(self, interaction: discord.Interaction):
        self.index = min(self.index + self.max_per_page, len(self.servers))
        await interaction.response.edit_message(embed=self.get_embed())

    def get_embed(self):
        embed = Embed(title="Tracked Servers", color=discord.Color.dark_blue())
        for i in range(self.index, min(self.index+self.max_per_page, len(self.servers))):
            sid, data = self.servers[i]
            embed.add_field(name=f"Server {sid}", value=f"Players: {len(data.get('players',[]))}", inline=False)
        embed.set_footer(text=f"Showing {self.index+1}-{min(self.index+self.max_per_page,len(self.servers))} of {len(self.servers)} servers")
        return embed

@tree.command(name="list", description="List servers with last scan")
async def list_servers(interaction: discord.Interaction):
    if not executor_data:
        await interaction.response.send_message("No server data available.")
        return
    view = ServerListView(executor_data)
    await interaction.response.send_message(embed=view.get_embed(), view=view)

# -------------------- Scan Command --------------------
@tree.command(name="scan", description="Show latest scan of a server")
@app_commands.describe(server_id="Server Job ID to view scan")
async def scan(interaction: discord.Interaction, server_id: str):
    data = executor_data.get(server_id)
    if not data:
        await interaction.response.send_message(f"No scan data for server {server_id}")
        return
    embed = Embed(title=f"Scan: Server {server_id}", color=discord.Color.dark_blue())
    embed.add_field(name="PlaceId", value=str(data.get("placeId", "Unknown")))
    embed.add_field(name="Players", value=str(len(data.get("players", []))))
    embed.add_field(name="NPC Issues", value=", ".join(data.get("npcIssues", ["None"]))[:1024])
    await interaction.response.send_message(embed=embed)

# -------------------- Game Files with Pagination --------------------
class GameFilesView(View):
    def __init__(self, data):
        super().__init__(timeout=None)
        self.data = data
        self.entries = []
        for plr in data.get("players", []):
            if plr.get("glitches"):
                self.entries.append((f"{plr['name']} (Player)", ", ".join(plr["glitches"])))
        npc_issues = data.get("npcIssues", [])
        for npc in npc_issues:
            self.entries.append((npc, "NPC anomaly"))
        self.index = 0
        self.max_per_page = 5
        self.prev_button = Button(label="⬅️", style=ButtonStyle.blurple)
        self.next_button = Button(label="➡️", style=ButtonStyle.blurple)
        self.prev_button.callback = self.prev_page
        self.next_button.callback = self.next_page
        self.add_item(self.prev_button)
        self.add_item(self.next_button)

    async def prev_page(self, interaction: discord.Interaction):
        self.index = max(self.index - self.max_per_page, 0)
        await interaction.response.edit_message(embed=self.get_embed())

    async def next_page(self, interaction: discord.Interaction):
        self.index = min(self.index + self.max_per_page, len(self.entries))
        await interaction.response.edit_message(embed=self.get_embed())

    def get_embed(self):
        embed = Embed(title=f"Game Files: Server {self.data.get('jobId','Unknown')}", color=discord.Color.dark_blue())
        for i in range(self.index, min(self.index+self.max_per_page, len(self.entries))):
            embed.add_field(name=self.entries[i][0], value=self.entries[i][1], inline=False)
        embed.set_footer(text=f"Showing {self.index+1}-{min(self.index+self.max_per_page,len(self.entries))} of {len(self.entries)} entries")
        return embed

@tree.command(name="gamefiles", description="Show detected glitches and anomalies")
@app_commands.describe(server_id="Server Job ID to view full game file report")
async def gamefiles(interaction: discord.Interaction, server_id: str):
    data = executor_data.get(server_id)
    if not data:
        await interaction.response.send_message(f"No data for server {server_id}")
        return
    view = GameFilesView(data)
    await interaction.response.send_message(embed=view.get_embed(), view=view)

# -------------------- Run Bot --------------------
bot.run(TOKEN)
