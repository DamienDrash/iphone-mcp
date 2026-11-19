# Security

iphone-mcp gives an AI agent control over a real phone. Please read the *Safety* section of the README.

- WebDriverAgent listens on port 8100 on the iPhone **without authentication** while it runs.
  Only run it on networks you trust and stop it when you are done (`iphone-mcp stop`).
- iphone-mcp never stores passwords or passcodes and never types them.

## Reporting a vulnerability

Please do **not** open a public issue. Use GitHub's
[private vulnerability reporting](https://github.com/DamienDrash/iphone-mcp/security/advisories/new) instead.
You will get an answer within a few days.
