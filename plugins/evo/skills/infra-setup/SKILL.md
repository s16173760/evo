---
name: infra-setup
description: Non-user-invocable provider/setup reference for evo backend switching, prerequisite checks, and auth/install guidance.
disable-model-invocation: true
---

# Infra Setup

Use this when the user wants to change where experiments run: local worktrees, pool slots, or a remote provider such as Modal, E2B, Daytona, AWS, Hetzner, SSH, manual sandbox-agent, or a custom dotted-path provider.

## Goals

- Be explicit about the target backend/provider.
- Check prerequisites before mutating evo config.
- Never install provider SDKs silently.
- Give one actionable auth command per provider.

## Flow

1. Identify the target:
   - `worktree` or `pool` means local backends.
   - `modal`, `e2b`, `ssh:...`, or another remote spec means `backend=remote`.
2. If the target is remote, parse the provider choice the same way evo CLI does:
- `modal`
- `e2b`
- `daytona`
- `aws`
- `hetzner`
- `manual`
- `ssh:user@host[:port]`
   - another built-in provider name
   - dotted import path for a custom provider
3. Check whether evo itself appears to be installed via `pipx`, `uv tool`, or a venv by calling `python -c "from evo.providers import detect_install_method; print(detect_install_method())"`.
4. For SDK-backed providers, verify the SDK import. If missing, ask the user before installing it.
   - If `evo` was installed with `uv tool` or `pip`/`venv`, prefer the matching extra on `evo-hq-cli`:
     - `uv-tool`: `uv tool install --reinstall 'evo-hq-cli[<provider-extra>]'`
     - `venv` / `pip`: `python -m pip install 'evo-hq-cli[<provider-extra>]'`
   - If `evo` was installed with `pipx`, inject the provider SDK into the same `evo-hq-cli` environment:
     - `pipx`: `pipx inject evo-hq-cli <provider-sdk>`
5. Check auth and show exactly one provider-specific auth command or setup step. Use `references/provider-matrix.md`.
6. Once prerequisites are satisfied, run the explicit config command:

```bash
evo config backend remote --provider <provider> --provider-config ...
```

Or for local backends:

```bash
evo config backend worktree
evo config backend pool --workspaces /abs/slot-a,/abs/slot-b
```

7. Be explicit that incomplete provider setup usually surfaces on
   `evo new --remote <provider> ...`, because that is where remote
   allocation and bootstrap actually happen.

## Pre-assumptions

Before trying to switch a workspace to a remote provider, confirm the basics:

- the target backend is clear from the user's request; only ask if the
  intent is genuinely ambiguous between `worktree`, `pool`, and `remote`
- the machine running evo has the right provider SDK or transport installed
- the user has auth for that provider available now, not "somewhere else"
- the provider-specific minimum config exists
  - `modal`: auth + optional config
  - `e2b`: API key + optional config
  - `daytona`: API key and API URL/target if needed
  - `aws`: creds, region, image, SSH key pair/private key, and usually network config
  - `hetzner`: token, server type, SSH key/private key, and image/location choices
  - `ssh`: reachable host, working SSH user, and key/port if needed
  - `manual`: reachable sandbox-agent URL and bearer token
- for SSH-backed VM providers, the guest assumptions are plausible before allocation:
  - the image enables SSH
  - the SSH user matches the image
  - the image architecture matches the selected instance type
  - the host can install and run `sandbox-agent`

## Provider notes

See `references/provider-matrix.md` for the compact provider summary, common config, and provider-specific setup/auth command.
