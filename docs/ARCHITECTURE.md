# beeperclaw Architecture

## Overview

beeperclaw is a Matrix bot that bridges mobile messaging (via Beeper) to the OpenCode coding agent. This allows developers to assign coding tasks from their phone and have them executed on their development machine.

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           MOBILE DEVICE                                  │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                    Beeper App (iOS/Android)                        │  │
│  │                                                                    │  │
│  │  User sends: /build fix the authentication bug in login.ts        │  │
│  │  User sends: /plan analyze the payment processing module          │  │
│  │  User sends: /status                                               │  │
│  └───────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ Matrix Protocol
                                    │ (End-to-End Encrypted)
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        BEEPER MATRIX SERVER                              │
│                      (matrix.beeper.com)                                 │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ Matrix Protocol
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      DEVELOPMENT MACHINE (Mac)                           │
│                                                                          │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                     beeperclaw Bot (Python)                          │  │
│  │                                                                    │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐   │  │
│  │  │   Matrix    │  │   Command   │  │    OpenCode Client      │   │  │
│  │  │   Client    │──│   Handler   │──│    (HTTP API)           │   │  │
│  │  │             │  │             │  │                         │   │  │
│  │  └─────────────┘  └─────────────┘  └─────────────────────────┘   │  │
│  │         │                │                      │                 │  │
│  │         │                │                      │                 │  │
│  │         ▼                ▼                      ▼                 │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐   │  │
│  │  │   Event     │  │   Session   │  │    Provider Manager     │   │  │
│  │  │   Monitor   │  │   Manager   │  │    (Fallback Chain)     │   │  │
│  │  └─────────────┘  └─────────────┘  └─────────────────────────┘   │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                    │                                     │
│              ┌─────────────────────┼─────────────────────┐              │
│              ▼                     ▼                     ▼              │
│  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐   │
│  │   OpenCode        │  │   GitHub API      │  │   AI Providers    │   │
│  │   Server          │  │                   │  │                   │   │
│  │   :4096           │  │   Issues, PRs     │  │   Antigravity     │   │
│  │                   │  │   Assignments     │  │   Copilot Pro+    │   │
│  │   - Sessions      │  │   Comments        │  │   HuggingFace     │   │
│  │   - Messages      │  │                   │  │                   │   │
│  │   - Agents        │  │                   │  │                   │   │
│  │   - MCP Tools     │  │                   │  │                   │   │
│  └───────────────────┘  └───────────────────┘  └───────────────────┘   │
│              │                                                          │
│              ▼                                                          │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                    Your Codebase / Projects                        │  │
│  └───────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

## Components

### 1. Matrix Client (simplematrixbotlib)

The Matrix client handles all communication with the Beeper Matrix server:

- **Authentication**: Login with username/password or access token
- **Message Handling**: Receive and parse incoming messages
- **Response Sending**: Send formatted responses back to users
- **E2EE Support**: End-to-end encryption for secure communication

### 2. Command Handler

Parses incoming messages and routes them to appropriate handlers:

- **Command Parsing**: Extract command name and arguments
- **Authorization**: Check if user is allowed to use the bot
- **Rate Limiting**: Prevent abuse
- **Error Handling**: Graceful error responses

### 3. OpenCode Client

HTTP client for the OpenCode Server API:

- **Session Management**: Create, list, delete sessions
- **Message Sending**: Send prompts to agents
- **Event Subscription**: Monitor task completion via SSE
- **Command Execution**: Execute slash commands

### 4. Session Manager

Manages the mapping between Matrix conversations and OpenCode sessions:

- **Session Tracking**: Track active sessions per user/room
- **Session Lifecycle**: Create, reuse, and cleanup sessions
- **State Persistence**: Remember session state across restarts

### 5. Provider Manager

Handles AI provider selection and fallback:

- **Primary Provider**: Antigravity Manager (free)
- **Fallback Chain**: Copilot Pro+ → HuggingFace Pro
- **Quota Monitoring**: Detect when to switch providers
- **Model Selection**: Choose appropriate model for task

### 6. Event Monitor

Monitors OpenCode events for task completion:

- **SSE Subscription**: Subscribe to server-sent events
- **Completion Detection**: Detect when tasks finish
- **Notification**: Notify users of task completion

## Data Flow

### Sending a Task

```
1. User sends "/build fix auth bug" via Beeper
2. Message arrives at beeperclaw via Matrix
3. Command handler parses the command
4. Session manager gets/creates OpenCode session
5. OpenCode client sends message to server
6. OpenCode server processes with build agent
7. beeperclaw sends "Task started" response
8. Event monitor watches for completion
9. On completion, beeperclaw notifies user
```

### Checking Status

```
1. User sends "/status" via Beeper
2. Command handler routes to status command
3. OpenCode client queries session status
4. Response formatted and sent back
```

## Security Considerations

### Authentication

- Matrix E2EE for message transport
- Access token storage in secure location
- User allowlist for bot access

### API Security

- OpenCode server runs on localhost only
- Antigravity Manager runs on localhost only
- No external API exposure required

### Data Privacy

- All processing happens locally
- No data sent to external servers (except AI providers)
- Session data stored locally

## Scalability

### Current Design (Single User)

- Single bot instance
- Single OpenCode server
- Local processing only

### Future Considerations

- Multi-user support with session isolation
- Queue system for concurrent tasks
- Distributed processing

## Configuration

### Required

- Matrix homeserver URL
- Bot credentials
- OpenCode server URL

### Optional

- GitHub token for integration
- Custom AI provider settings
- Logging configuration

## Dependencies

### Python Packages

- `simplematrixbotlib`: Matrix bot framework
- `matrix-nio`: Matrix protocol implementation
- `httpx`: Async HTTP client
- `pydantic`: Configuration validation
- `click`: CLI framework
- `rich`: Terminal formatting

### External Services

- Beeper Matrix server (or any Matrix homeserver)
- OpenCode server (local)
- Antigravity Manager (local, optional)
