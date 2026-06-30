# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ElliNet13Launcher is a Python-based Minecraft launcher that downloads and launches Minecraft versions from Mojang's official CDNs. It supports both offline (cracked) and Microsoft account authentication.

## Running the Launcher

```bash
# Install dependencies
pip install -r requirements.txt

# Start GUI (default)
python launcher.py

# Command-line mode with specific version
python launcher.py -v 1.21.4

# Download without launching
python launcher.py -v 1.21.4 --dont-run

# Specify download worker threads
python launcher.py -v 1.21.4 -w 50
```

## Architecture

### Core Components

- **`launcher.py`** - Main entry point containing:
  - GUI using CustomTkinter (dark theme, version selector with history)
  - CLI argument parsing
  - Minecraft launch process (JVM args, classpath construction)
  - Microsoft authentication flow integration

- **`libs/pack_downloader.py`** - Handles asset/version fetching:
  - `grab_minecraft_manifest()` - Fetches version manifest from Mojang
  - `grab_version_json()` - Gets version-specific JSON
  - `download_assets()` - Concurrent asset downloading with ThreadPoolExecutor
  - `download_java_runtime()` - Downloads platform-specific Java runtime
  - Architecture detection for Windows/Linux/macOS (x64/x86/arm64)

- **`libs/mcauth3/mcauth3.py`** - Microsoft OAuth authentication:
  - Device code flow for Microsoft login
  - Xbox Live authentication (XBL)
  - XSTS token exchange
  - Minecraft services login
  - Token refresh support

### Data Directories

- **`minecraft_runtime/`** - Downloaded game files:
  - `versions/<version>/` - Version JSON and client JAR
  - `libraries/` - Minecraft libraries
  - `assets/` - Game assets
  - `runtimes/` - Java runtime executables

- **`minecraft/`** - User game data (saves, resourcepacks, logs, crash reports)

### Configuration

- **`config.properties`** - Runtime configuration (auto-created from template):
  - `username` - Player name for offline mode
  - `use_microsoft_account` - Boolean for auth method
  - `geometry` - GUI window size/position
  - `last_versions_list` - Compressed version history
  - `do_not_share_-_microsoft_account_refresh_token` - Stored Microsoft refresh token

### Key Implementation Details

1. **Version Downloads**: Downloads manifest from `https://launchermeta.mojang.com/mc/game/version_manifest.json`, then fetches version-specific JSON and assets from Mojang CDNs

2. **Java Runtime**: Dynamically detects OS/architecture and downloads the correct runtime from Mojang's Java runtime manifest

3. **Authentication Flow**:
   - Offline mode: Uses UUID `00000000-0000-0000-0000-000000000000` with access token `0`
   - Microsoft: Device code OAuth → XBL → XSTS → Minecraft Services → Profile fetch

4. **Concurrent Downloads**: Uses `ThreadPoolExecutor` with configurable worker count (default 30)

5. **Natives Extraction**: Extracts native libraries (.dll/.so/.dylib) from JAR files to version-specific `natives/` directory