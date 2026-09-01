# PRE-003 R2 hotfix

This temporary review note documents why the hotfix exists: the Windows self-hosted runner has Windows PowerShell (`powershell.exe`) but not PowerShell 7 (`pwsh`). The workflow therefore uses `shell: powershell`, and the idempotency JSON reader accepts the UTF-8 BOM emitted by Windows PowerShell 5.1.
