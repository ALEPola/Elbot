import platform
import psutil
import nextcord
from nextcord.ext import commands, tasks
from datetime import datetime, timedelta
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger("diagnostic")

ADMIN_EMAIL = "admin@example.com"
SMTP_SERVER = "smtp.example.com"
SMTP_PORT = 587
SMTP_USERNAME = "your_username"
SMTP_PASSWORD = "your_password"

class DiagnosticCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.start_time = datetime.now()
        self.health_check_loop.start()

    @nextcord.slash_command(name="uptime", description="Check the bot's uptime.")
    async def uptime(self, interaction: nextcord.Interaction):
        uptime_duration = datetime.now() - self.start_time
        uptime_str = str(timedelta(seconds=int(uptime_duration.total_seconds())))
        await interaction.response.send_message(f"🕒 Uptime: {uptime_str}")

    @nextcord.slash_command(name="ping", description="Check the bot's latency.")
    async def ping(self, interaction: nextcord.Interaction):
        latency = round(self.bot.latency * 1000)  # Convert to ms
        await interaction.response.send_message(f"🏓 Latency: {latency}ms")

    @nextcord.slash_command(name="cogs", description="List all loaded cogs.")
    async def cogs(self, interaction: nextcord.Interaction):
        loaded_cogs = list(self.bot.cogs.keys())
        cogs_list = "\n".join(loaded_cogs) if loaded_cogs else "No cogs loaded."
        await interaction.response.send_message(f"📂 Loaded Cogs:\n{cogs_list}")

    @nextcord.slash_command(name="system_info", description="Get system information.")
    async def system_info(self, interaction: nextcord.Interaction):
        system = platform.system()
        release = platform.release()
        version = platform.version()
        cpu = platform.processor()
        memory = psutil.virtual_memory().total / (1024 ** 3)  # Convert to GB
        await interaction.response.send_message(
            f"🖥 **System Information:**\n"
            f"- OS: {system} {release} ({version})\n"
            f"- CPU: {cpu}\n"
            f"- Memory: {memory:.2f} GB"
        )

    async def notify_admin(self, subject, message):
        """Send an email notification to the administrator."""
        try:
            msg = MIMEMultipart()
            msg["From"] = SMTP_USERNAME
            msg["To"] = ADMIN_EMAIL
            msg["Subject"] = subject

            msg.attach(MIMEText(message, "plain"))

            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.send_message(msg)

            logger.info("Admin notified successfully.")
        except Exception as e:
            logger.error(f"Failed to notify admin: {e}")

    @tasks.loop(minutes=5)
    async def health_check_loop(self):
        """Periodic task to monitor the bot's health."""
        try:
            latency = self.bot.latency
            logger.info(f"Bot is healthy. Latency: {latency:.2f} seconds.")
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            await self.notify_admin("Bot Health Check Failed", f"An error occurred: {e}")

    @health_check_loop.before_loop
    async def before_health_check(self):
        await self.bot.wait_until_ready()

def setup(bot):
    bot.add_cog(DiagnosticCog(bot))
    print("✅ Loaded DiagnosticCog")