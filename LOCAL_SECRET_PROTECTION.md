# Local Secret Protection (Windows)

This repo now includes:
- `.pre-commit-config.yaml`
- `.gitleaks.toml`
- `.secrets.baseline`

These checks block commits that contain API keys/secrets.

## 1. Install tools

Use one of the following:

```powershell
pip install pre-commit detect-secrets
```

If `gitleaks` is not already installed:

```powershell
winget install gitleaks.gitleaks
```

## 2. Enable git hooks

Run in repo root:

```powershell
pre-commit install
pre-commit install --hook-type commit-msg
```

## 3. First full scan

```powershell
pre-commit run --all-files
```

If needed, run gitleaks directly:

```powershell
gitleaks detect --source . --config .gitleaks.toml --redact --verbose
```

## 4. Keep baseline up to date

If detect-secrets flags known false positives and you approve them:

```powershell
detect-secrets scan --all-files --baseline .secrets.baseline
```

Then review the baseline diff before commit.

## 5. Emergency response (if secret leaked)

1. Revoke/rotate the leaked key immediately.
2. Remove secret from tracked files.
3. Re-scan:
```powershell
pre-commit run --all-files
```
4. Commit only after checks pass.

## 6. Team rules

- Never commit `.env`.
- Keep real keys only in GitHub Secrets or local environment.
- Use placeholder values in examples and docs.
