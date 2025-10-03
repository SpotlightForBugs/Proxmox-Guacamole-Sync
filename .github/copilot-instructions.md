# AI Coding Agent Instructions: Proxmox-Guacamole Sync

## 1. Core Directives & Rules of Engagement

These are the most critical rules that govern your behavior. They override any other instruction.

### 1.1. Ask First, Don't Guess (Principle of Certainty)
**REQUIRED**: If you are not 100% certain about any of the following, you **MUST STOP** and ask for human clarification:
* The safety implications of a security warning.
* The correct or most effective way to fix an issue.
* Whether a design pattern follows established best practices.
* The user's specific requirements or intent.

### 1.2. Think Critically, Verify Everything
**REQUIRED**: When encountering warnings, errors, or potential security issues, you **MUST** follow this process:
1.  **ASK**: If unsure about the severity, ask the human.
2.  **RESEARCH**: Use the `vscode-websearchforcopilot_webSearch` tool to research the specific warning, error, or vulnerability pattern *before* drawing a conclusion.
3.  **NEVER ASSUME**: Never self-declare that a problem is "acceptable" or a "false positive" without explicit human verification or strong evidence from research.

**BAD Example**: *"These 16 security warnings are all false positives and acceptable for this use case..."*

**GOOD Example**: *"I found 16 security warnings. I will now research the 'B501' and 'SSRF' warning types. My initial research suggests B501 is related to SSL verification. I need clarification: Are you intentionally disabling SSL verification for self-signed certificates in a production context? This could be a security risk."*

### 1.3. Use Web Search Aggressively
**MANDATORY**: You **MUST** use the `vscode-websearchforcopilot_webSearch` tool to verify your understanding *before* making conclusions about:
* Security warnings and their implications.
* Library, framework, and language best practices.
* API usage patterns and error handling.
* The meaning and root cause of error messages.

---

## 2. Absolute Prohibitions (NEVER Statements)

### 2.1. NO Emojis
**STRICTLY FORBIDDEN**: Do not use emojis in any output. This includes:
* LLM responses to the user.
* Code files (`.py`, `.yaml`, `.md`, etc.).
* Documentation and code comments.
* Terminal output.
* Git commit messages.

**Rationale**: Emojis introduce visual noise, cause compatibility issues in terminals, and reduce the professionalism of this infrastructure tool.

**Use neutral Unicode symbols instead**:
* `●`, `○`, `*` for lists/bullets.
* `✔`, `✓` for success indicators.
* `✗`, `×` for failure indicators.
* `⚠`, `!` for warnings.
* `ℹ`, `i` for informational messages.

**BANNED Behavior**: `🎉 Success! All connections synced!`
**CORRECT Behavior**: `✓ Success! All connections synced!`

### 2.2. NO "Everything is Fine" Scripts
**STRICTLY FORBIDDEN**: Do not create Python scripts whose sole purpose is to validate existing code and report that "all is good" or "no issues were found." If checks are needed, use direct terminal commands.

### 2.3. NO Summary or Status Files
**STRICTLY FORBIDDEN**: Do not create repository files for the purpose of summarizing your own work, such as `SECURITY_SCANNER_FINDINGS.md`, `STATUS.md`, `REVIEW.md`, or `ANALYSIS.md`.

**Rationale**: These files add clutter and provide no long-term value.

---

## 3. Project Technical Requirements

### 3.1. Zero Type Errors
**CRITICAL REQUIREMENT**: All code changes **MUST** result in zero type-checking errors. The codebase must pass `mypy --ignore-missing-imports` without any errors. Any change that introduces a new type error will be rejected.

### 3.2. UV Package Manager Mandate
**MANDATORY**: All Python-related commands **MUST** use the `uv` package manager.
* For running scripts: `uv run python ...`
* For package management: `uv pip ...`
* Never use bare `python` or `pip` commands.

---

## 4. Project Overview & Architecture

### 4.1. Project Goal
This project is a single-file Python tool (`guac_vm_manager.py`) that synchronizes virtual machines from a Proxmox VE environment to an Apache Guacamole instance. It does this by parsing credential information stored in the Proxmox VM's "Notes" field and using it to create remote desktop connections (RDP, VNC, SSH) via the Guacamole API.

### 4.2. System Architecture
* **Core Components**:
    * `GuacamoleAPI`: Manages authentication and CRUD operations for connections and groups.
    * `ProxmoxAPI`: Discovers VMs and parses credentials from their notes.
    * `NetworkScanner`: Finds VM IP addresses using ARP and ping when the Proxmox Guest Agent is unavailable.
    * `WakeOnLan`: A native Python implementation for waking powered-off machines.
* **Key Data Flow**:
    1.  **Discover**: Fetch all VMs from Proxmox.
    2.  **Parse**: Extract credential configurations from each VM's notes.
    3.  **Resolve**: Determine the VM's IP address, first via the Guest Agent, then falling back to network scanning.
    4.  **Sync**: Create or update the corresponding connection and connection group in Guacamole.

### 4.3. Critical Implementation Patterns
* **VM Notes Credential Format**: The core logic relies on parsing a flexible key-value format from the Proxmox VM notes.
    ```
    user:"admin" pass:"password" protos:"rdp,ssh" rdp_port:"3390" confName:"{vmname}-{user}-{proto}";
    ```
    * **Logic**: Located in `parse_credentials_from_notes()`.
    * **Features**: Order-independent parameters, multiple protocols, and template variables (`{vmname}`, `{user}`, `{vmid}`, etc.).
* **Configuration**: A simple class-based configuration is loaded from `config.py` (copied from `config_example.py`).
* **API Resilience**: Both API clients attempt multiple endpoint paths and data sources before failing, ensuring graceful handling of different Guacamole/Proxmox versions.
* **PVE Source Tracking**: The script caches a mapping of Guacamole connections to their source Proxmox nodes to provide context in listings and prevent duplicate processing.

---

## 5. Development & CLI Reference

### 5.1. Development & Testing Workflow
* **Setup**:
    ```bash
    # 1. Create and edit your configuration file
    cp config_example.py config.py

    # 2. Install dependencies using uv
    uv pip install -r requirements.txt
    ```
* **Core Testing Commands**:
    ```bash
    # Test API authentication to both services
    uv run python guac_vm_manager.py test-auth

    # Debug the VM discovery and credential parsing process
    uv run python guac_vm_manager.py debug-vms

    # Test the network scanner for a specific MAC address
    uv run python guac_vm_manager.py test-network "aa:bb:cc:dd:ee:ff"
    ```

### 5.2. CLI Usage Patterns & Features
The tool uses `Typer` to provide a modern CLI with subcommands and rich help.
* **Key Commands**:
    ```bash
    # Run the full interactive menu (default action)
    uv run python guac_vm_manager.py interactive

    # Automatically sync all VMs with credentials
    uv run python guac_vm_manager.py auto
    uv run python guac_vm_manager.py auto --force

    # Add a single VM via an interactive menu
    uv run python guac_vm_manager.py add

    # List connections with regex filtering
    uv run python guac_vm_manager.py list --connection ".*-admin-.*" --protocol rdp

    # Edit or delete connections/groups with regex and force flags
    uv run python guac_vm_manager.py edit --connection "vm-.*" --hostname 192.168.1.100
    uv run python guac_vm_manager.py delete --connection "temp-.*" --force

    # Add a non-Proxmox host
    uv run python guac_vm_manager.py add-external --hostname server.com
    ```
* **CLI Enhancements**:
    * **Partial Option Support**: If a command is run with missing required options (e.g., `add --vm-id 100`), it will prompt the user for the missing information instead of failing.
    * **Advanced Pattern Matching**: Most commands support regex and comma-separated patterns for bulk operations on connections and groups.

---

## 6. Project Conventions & Style

### 6.1. Code Architecture
* **Single-File Architecture**: All core logic resides within `guac_vm_manager.py`. Maintain this convention.
* **Minimal Dependencies**: `requests`, `urllib3`, `cryptography`, `typer`, `rich`.
* **No ORM**: All API interaction is done via direct REST calls and manual JSON handling.

### 6.2. UI/UX and Output
* **Rich Output**: The script uses the `rich` library for formatted tables, panels, and progress indicators.
* **Raw Mode**: A global `--raw` flag or `GUAC_RAW_MODE=1` environment variable disables all rich formatting for use in logs, CI/CD, or accessibility tools.
* **Animations**: A simple `⬢ → ⬢→⬢ → ⬢` animation provides visual feedback during sync operations. It is automatically disabled in raw mode.

### 6.3. Error Handling & State Management
* **Progressive Fallback**: The preferred style is to try multiple methods before failing (e.g., API endpoints, IP discovery methods).
* **VM State Management**: The script can automatically start stopped VMs to perform network scans and restores them to their original power state afterward.

### 6.4. Security Patterns
* **Credential Isolation**: The `config.py` file is git-ignored. Sensitive credentials for remote machines are stored exclusively in the Proxmox VM notes.
* **Password Encryption**: The tool supports optional Fernet-based encryption for passwords stored in the notes.
* **SSL**: SSL certificate verification is disabled by default to support environments with self-signed certificates.

---

## 7. Code-Specific Directives

The following Pylint disable comment **MUST** be present at the top of the `guac_vm_manager.py` file to reduce noise from intentional design patterns (like late imports for performance).

```python
# Pylint: some imports intentionally live inside functions to avoid heavy startup
# or circular imports. Also some 'pass' statements are used intentionally to
# silence non-critical exceptions in probing code paths. Disable the following
# checks at module level to reduce noisy warnings.
# pylint: disable=import-outside-toplevel, unnecessary-pass