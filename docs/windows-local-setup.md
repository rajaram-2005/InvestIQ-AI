# Windows local-development setup

This is a **proposed development setup**, not yet verified on Windows or with a real local Supabase stack. The Arena preview runs on a separate machine; localhost on your computer is not the Arena preview. The current prototype is not production-ready: authentication, local Docker networking, privacy/deletion, backups and end-to-end behavior still need verification. Use fictional test data only until these have been reviewed.

## 1. Install prerequisites

On a 64-bit Windows computer install:

- Docker Desktop with the WSL 2 backend enabled and running (follow Docker's Windows installation instructions and restart if requested).
- Node.js 22 LTS and npm.
- Python 3.11 (64-bit), with the Python launcher (`py`).

Internet access is needed to install packages and download Docker images. Supabase CLI 3.2.0 is run via `npx.cmd`; it does not require a Supabase cloud login or project. A first start can take several minutes and requires free disk space. Confirm installation in PowerShell:

```powershell
docker --version
node --version
npm.cmd --version
py -3.11 --version
```

Obtain the **current project files** from this Arena workspace and open a PowerShell terminal in the repository folder (the folder containing `README.md`). Work in this session is not necessarily committed or pushed, so cloning the GitHub repository alone might not include the latest files. Do not paste passwords, access keys or status output into chat.

## 2. Install dependencies

From the project root in PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
npm.cmd --prefix web ci
```

The dependency lock was generated and tested on Linux, not Windows. If a dependency does not install on Windows, note the package and error (redact secrets) before changing the lock. Do not assume that installing the packages verifies every research feature.

## 3. Start local Supabase

Make sure Docker Desktop is running, then in the same repository folder:

```powershell
npx.cmd --yes supabase@3.2.0 start
```

The CLI starts local Docker containers and applies migrations on initial setup. `supabase/config.toml` exposes only the default `public`/`graphql_public` API schemas, not the InvestIQ application or evidence schemas; no example financial data is seeded. Defaults use API port 54321, PostgreSQL 54322 and local Studio 54323. If a port is in use, resolve the conflict before proceeding. The API helper intentionally accepts only the default local ports. The CLI may print **local development credentials**: keep terminal output private, never paste it into chat, and never reuse local keys or passwords in production.

Open the local Studio at `http://127.0.0.1:54323`. Under **Authentication → Users**, create a test user and confirm it using the Studio controls, if prompted. Use a test-only email and a unique password. No user is created or password chosen by this repository. The app currently has sign-in but no sign-up UI. Studio labels may vary with the installed version.

## 4. Start the backend and frontend

Open **two PowerShell terminals** in the same project folder. In the first:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-local-api.ps1
```

The script reads local CLI status into memory, checks expected local addresses, sets the required environment variables only for the API process and starts FastAPI on loopback. It opts in to HTTP **only for** `http://127.0.0.1:54321`; it does not allow arbitrary insecure remote auth URLs. It does not write keys to a file. The local DB URL supplied by the CLI normally uses a powerful development database role; the application switches to `authenticated` for its queries. This is not an acceptable production credential setup.

In the second terminal:

```powershell
cd web
npx.cmd next dev --hostname 127.0.0.1 --port 3000
```

Open **http://localhost:3000** in a modern browser and sign in with the test user. `localhost` is treated as a secure context by modern browsers for the `Secure` session cookie; if sign-in does not persist, check browser cookie settings and use the exact address above. You can check `http://localhost:3000/api/v1/status` in the browser; it should show authentication and database as configured. This indicates only that configuration exists, **not** that credentials, migrations or database connectivity are verified. If a migration failed, do not enter personal data; save a redacted error message and investigate.

## 5. Security and data safety

- This is a development stack with known local credentials. It may bind Docker-published ports more broadly than expected depending on Docker/Windows configuration; use Windows Firewall to prevent network access, verify port bindings, and **do not expose the stack or dev servers to the internet or other devices**. Secure your Windows account and use full-disk encryption where available. Localhost is not protection against malware or other users with access to your PC.
- The existing prototype does not implement user deletion, server-side token revocation, independently trusted audits or a backup/restore workflow. Consent withdrawal is not deletion. Avoid real financial or other sensitive records until those controls are implemented and tested.
- Stopping the frontend/backend with Ctrl+C stops them. From the project root, `npx.cmd --yes supabase@3.2.0 stop` stops the local database stack while retaining data volumes by default. **Do not use** `stop --no-backup`, `db reset`, or delete Docker volumes if you want to keep data; those operations can destroy local data. Backups and restoration must be set up and tested separately; merely retaining a Docker volume is not a backup.
- We have not connected market feeds, deployed cloud services, or changed your Supabase or Vercel projects.

## Troubleshooting

- Docker not running: start Docker Desktop and wait until it reports that the engine is ready; retry the start command.
- `py -3.11` unavailable: install the 64-bit Python 3.11 launcher and reopen PowerShell.
- PowerShell blocks `.ps1` files: the specific invocation above uses a process-only execution-policy override, not a permanent policy change.
- `Unexpected local Supabase status format`: CLI output or port settings differ. Do not paste status output publicly; the developer should inspect the version/format locally. Do not weaken the script's local-address checks.
- Sign-in says authentication isn't configured: ensure the API was started with the script and verify the status endpoint. An HTTP 503 can also mean the auth service/database is unavailable. Local sessions expire; sign in again.
- Keep the command output redacted when sharing an error; never share database URLs, passwords, cookies, tokens or keys.
