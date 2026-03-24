# General Instructions
- The user is working on a Windows operating system.
- Always provide and execute terminal commands that are fully compatible with Windows (Command Prompt or PowerShell).
- Avoid Unix-specific commands like `ls`, `cat`, `rm`, `export`, or `grep` unless executing within a bash-like environment. Instead, use equivalent PowerShell or CMD commands (e.g., `dir`, `type`, `Remove-Item`, `set`, `$env:VAR`, `Select-String`).
- For Python environments, remember to use Windows paths for virtual environment activation (e.g., `venv\Scripts\activate` instead of `venv/bin/activate`).
- Handle file paths appropriately for Windows when writing platform-specific scripts.