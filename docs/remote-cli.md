# Remote CLI (mosh + tmux)

This guide shows a resilient SSH workflow for mobile connections using mosh + tmux. It keeps sessions alive when your network drops or your phone sleeps.

## Phone quick start

Replace `mihai` and the example host below with your own username and machine.

### Headscale IP examples

```bash
ssh mihai@100.64.0.42
mosh mihai@100.64.0.42
mosh --ssh="ssh -p 2222" mihai@100.64.0.42
```

### MagicDNS examples

```bash
ssh mihai@beeperclaw.tailnet.example
mosh mihai@beeperclaw.tailnet.example
mosh --ssh="ssh -p 2222" mihai@beeperclaw.tailnet.example
```

### iPhone and iPad

- `Moshi`: best fit if you want native `mosh` on iOS.
- `Termius`: good SSH client for quick access; pair it with `tmux` if you do not need `mosh`.

### Android

- `Termux`: install the tools locally, then run the same commands as above.

```bash
pkg update
pkg install -y mosh openssh tmux
```

## Install

macOS:

```bash
brew install mosh tmux
```

Ubuntu/Debian:

```bash
sudo apt-get update && sudo apt-get install -y mosh tmux
```

## Connect with mosh

Replace `your-host` with a Headscale IP or MagicDNS name.

```bash
mosh your-user@your-host
```

If your SSH server uses a non-default port:

```bash
mosh --ssh="ssh -p 2222" your-user@your-host
```

## Keep sessions with tmux

Start a named session:

```bash
tmux new -s beeperclaw
```

Detach:

```bash
tmux detach
```

Reattach later:

```bash
tmux attach -t beeperclaw
```

## Why this helps

- mosh survives network changes (Wi-Fi to cellular).
- tmux keeps your session alive across disconnects.
- Together, you can resume long-running tasks safely.
