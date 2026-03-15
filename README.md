# codebeep

> Your AI coding agent, accessible from anywhere via Matrix/Beeper.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](https://opensource.org/licenses/MIT)

**codebeep** is a self-hosted AI coding agent that lets you assign coding tasks from your phone via [Beeper](https://beeper.com) (Matrix protocol). It integrates with [OpenCode](https://opencode.ai) to provide the same powerful coding capabilities you have on desktop, but accessible from anywhere.

Inspired by [OpenClaw](https://github.com/openclaw/openclaw) - a personal AI assistant that runs on your own devices and answers on channels you already use.

## Recent Updates (February 2026)

- ✅ **Fixed KeyError**: Resolved 'createdAt' parsing issues when communicating with OpenCode API
- ✅ **Self-Reply Prevention**: Added defensive checks to prevent infinite bot response loops
- ✅ **Room Bootstrap**: Implemented automatic creation of unencrypted "CodeBeep Shell" room for commands
- ✅ **Enhanced Logging**: Improved debugging with comprehensive message flow logging
- ✅ **Error Handling**: Better fallback mechanisms for encrypted room handling

## Why codebeep?

| Feature | Cursor Slack | Copilot Slack | codebeep |
|---------|--------------|---------------|----------|
| Self-hosted | No | No | **Yes** |
| Open Source | No | No | **Yes** |
| Privacy | Cloud | Cloud | **Local** |
| Platform | Slack only | Slack only | **Matrix/Beeper** |
| AI Provider | Cursor models | GitHub models | **Any (Antigravity, Copilot, etc.)** |
| Cost | Paid | Paid | **Free*** |

*Free when using Antigravity Manager with Google AI Studio quotas

## Features

- **Mobile-first**: Assign coding tasks from your phone via Beeper/Matrix
- **OpenCode Integration**: Full access to OpenCode's build and plan agents
- **Self-hosted**: Your code, your data, your control
- **Unencrypted Shell Room**: Dedicated room for reliable command execution
- **Error Resilience**: Handles rate limiting and transient failures gracefully
- **Multi-Provider Support**: Works with any OpenCode-compatible AI provider

## Quick Start

### Prerequisites

- Python 3.11+
- Matrix account (either [Beeper](https://beeper.com) or [matrix.org](https://matrix.org))
- [OpenCode](https://opencode.ai) installed and configured

**Beeper note:** Starting a new Matrix DM from Beeper is supported on Desktop and Android, but is still a work in progress on iOS. If you plan to message a `@bot:matrix.org` account from Beeper, use Desktop or Android for the first DM. See Beeper’s Matrix chat guide for details.

### Installation

#### Method 1: Docker (Recommended)
```bash
# Clone the repository
git clone https://github.com/Mihai-Codes/codebeep.git
cd codebeep

# Build and run with Docker (solves python-olm compilation issues)
docker-compose up -d

# Check logs
docker-compose logs -f
```

#### Method 2: Native Python
```bash
# Clone the repository
git clone https://github.com/Mihai-Codes/codebeep.git
cd codebeep

# Create virtual environment and install
python3 -m venv venv
source venv/bin/activate
pip install -e .

# Configuration
cp config.example.yaml config.yaml
# Edit config.yaml with your Beeper credentials
```

### Auto-Start Options

#### Option 1: Docker (Recommended - Solves python-olm issues)
```bash
# Build and run with Docker
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down
```

#### Option 2: macOS LaunchAgent
This project includes a LaunchAgent to keep the bot running in the background.

1. **Install the Service:**
   ```bash
   cp com.mihai.codebeep.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.mihai.codebeep.plist
   ```

2. **Check Logs:**
   ```bash
   tail -f /tmp/codebeep.log
   tail -f /tmp/codebeep.error.log
   ```

## Configuration

Edit `config.yaml` (Beeper account):

```yaml
matrix:
  homeserver: "https://matrix.beeper.com"
  username: "@your-bot:beeper.local"
  password: "your-password"  # or use access_token

opencode:
  server_url: "http://127.0.0.1:4096"
  default_agent: "build"
```

Edit `config.yaml` (matrix.org account):

```yaml
matrix:
  homeserver: "https://matrix-client.matrix.org"
  username: "@your-bot:matrix.org"
  password: "your-password"  # or use access_token
```

### Matrix.org fallback (no Beeper+ required)

If you can’t create a second Beeper account, you can run the bot on matrix.org and DM it from Beeper Desktop/Android.

1. Create a Matrix account for the bot on matrix.org (Element signup is fine).
2. Exchange username/password for an access token:

```bash
curl -s https://matrix-client.matrix.org/_matrix/client/v3/login \
  -H "Content-Type: application/json" \
  -d '{
    "type":"m.login.password",
    "user":"@codebeep-bot:matrix.org",
    "password":"YOUR_BOT_PASSWORD"
  }'
```

3. Copy `access_token` into `.env` and restart:

```bash
BEEPER_ACCESS_TOKEN=PASTE_MATRIX_TOKEN_HERE
docker compose up -d
```
```

## Architecture

```
Phone (Beeper App)
        │
        │ Matrix Protocol (E2EE)
        ▼
codebeep Bot (Docker)
        │
        ├──► OpenCode Server (:4096)
        │         │
        │         └──► AI Agents (build, plan, general, etc.)
        │                   │
        │                   └──► MCP Tools, Code Execution
        │
        └──► Matrix Rooms
                  │
                  └──► CodeBeep Shell (unencrypted)
                            │
                            └──► Command Interface
```

## Available Commands

- `/build <task>` - Execute a coding task with full file access
- `/plan <request>` - Analyze code without making changes
- `/status` - Check current session status
- `/agents` - List available AI agents
- `/sessions` - List all sessions
- `/help` - Show help information

## Current Status

✅ **Working**: Basic commands (/help, /status, /agents), Docker deployment  
⚠️ **In Progress**: Action commands (/build, /plan) - working but may have occasional errors

## Known Issues

- Room creation may fail due to Matrix rate limiting (retries implemented)
- Some API response formats may cause intermittent errors (being addressed)

## Roadmap

See [ISSUES.md](ISSUES.md) for planned improvements:

1. Persistent KeyError investigation
2. Session state persistence
3. Robust error handling
4. Room creation reliability
5. Message deduplication

## License

MIT License - see [LICENSE](LICENSE) for details.

---

**Note**: This project is not built by the OpenCode team and is not affiliated with them in any way.
