# Security

iphone-mcp gives an AI agent control over a real phone. Please read the *Safety* section of the README.

- WebDriverAgent listens on port 8100 on the iPhone **without authentication** while it runs.
  Only run it on networks you trust and stop it when you are done (`iphone-mcp stop`).
  Never expose port 8100 to the internet; for remote use, use `iphone-mcp serve --http`, which requires a token.
- iphone-mcp never stores passwords or passcodes and never types them.

## Supported versions

| Version | Security fixes |
|---|---|
| 1.x (latest release) | ✅ |
| 0.x | ❌ – please upgrade |

## Reporting a vulnerability

Please do **not** open a public issue. Use GitHub's
[private vulnerability reporting](https://github.com/DamienDrash/iphone-mcp/security/advisories/new) instead.
Only the maintainer can see the report.

Helpful details: the affected version, how to reproduce it, and what an attacker could do (for example
control the phone, read the screen, or obtain the remote-mode token).

You will get a first answer within a few days. Confirmed issues are fixed in a new release and published as
a GitHub security advisory, crediting you unless you prefer otherwise.
