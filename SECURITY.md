# Security Policy

## Supported Versions

The following versions of Talaria are currently supported with security updates:

| Version | Supported          | Notes                                    |
| ------- | ------------------ | ---------------------------------------- |
| 0.1.x   | :white_check_mark: | Current release                          |
| < 0.1   | :x:                | No longer supported                      |

## Reporting a Vulnerability

We take security vulnerabilities seriously. If you discover a security issue, please report it responsibly.

**Please DO NOT file a public GitHub issue for security vulnerabilities.**

### How to Report

Send an email to **bryfeng@gmail.com** with the following information:

- A brief description of the vulnerability
- Steps to reproduce the issue
- Potential impact of the vulnerability
- Any suggested fixes (optional)

### What to Include

Please include as much detail as possible:

```
Subject: [SECURITY] Talaria Vulnerability Report

Vulnerability type:
Affected version(s):
Reproducer:
Impact:
Suggested fix (optional):
```

### Response Timeline

- **Acknowledgment**: Within 48 hours of report
- **Initial assessment**: Within 7 days
- **Fix timeline**: Depends on severity (critical: ASAP, low: next release)

### Scope

Talaria is a local-first project management tool. Security considerations include:

- Protection of local card data and board state
- Safe execution of AI agents spawned by the tool
- Secure handling of GitHub tokens and API credentials
- Protection against malicious card content

Talaria does NOT:
- Expose sensitive data to third parties by default
- Require internet connectivity to function
- Automatically execute untrusted code from card content (agents operate on user-approved cards)

## Security Updates

Security updates are released as patch versions. Subscribe to the [GitHub releases feed](https://github.com/bryfeng/talaria/releases.atom) to stay informed.
