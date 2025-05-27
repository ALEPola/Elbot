# ELBOT

ELBOT is a Discord bot designed to provide a variety of features, including music playback, Formula 1 race reminders, and more.

## Features
- Music playback with queue management.
- Formula 1 race schedules and reminders.
- Web portal for managing bot settings.

## Setup Instructions
1. Clone the repository:
   ```bash
   git clone https://github.com/ALEPola/ELBOT.git
   ```
2. Navigate to the project directory:
   ```bash
   cd ELBOT
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Set up environment variables in a `.env` file:
   ```env
   GUILD_ID=your_guild_id
   CHANNEL_ID=your_channel_id
   ICS_URL=your_ics_url
   ```
5. Run the bot:
   ```bash
   python main.py
   ```

## Deployment
- Use the provided `Dockerfile` for containerized deployment.
- CI/CD pipeline is set up using GitHub Actions.

## Contributing
See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.
