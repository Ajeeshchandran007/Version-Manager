# Server Configuration

Live server targets are intentionally stored outside `config.json`. This keeps
environment-level application settings separate from release-specific
infrastructure inventory.

## File Locations

Server configuration is loaded in this order:

1. `Input/teams/<team>/releases/<release>/servers.yml`
2. `Input/teams/<team>/servers.yml`
3. `Input/servers.yml`

Use `Input/servers.example.yml` as the committed template. Real release folders
are ignored by git in this project, so active release server files can remain
environment-local unless you intentionally force-add one.

## Format

```yaml
servers:
  OpenSSL:
    host: 192.168.2.5
    method: ssh
    user: es1service
    password: ${SSH_PASSWORD}
    command: openssl version
```

The file also supports a bare mapping without the top-level `servers` key.
Credential values should use environment variable placeholders such as
`${SSH_PASSWORD}`. Do not store real passwords in source-controlled YAML.

## Runtime Flow

`App/server_config.py` resolves the active team and release from the Streamlit
workspace context or from `input_files.software_yml` for MCP and CLI runs.
`Core/server_querier.py` consumes the resolved server map and keeps SSH/HTTP
query behavior unchanged.
