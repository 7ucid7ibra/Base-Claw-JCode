# BaseClaw Overlays

BaseClaw core should stay generic. Keep machine-specific behavior, credentials, private connectors, and local workflows outside the tracked source tree.

Use overlays for local setup that should survive BaseClaw updates without becoming part of the public product.

## Recommended Shape

```text
profiles/
  MyAgent/
    .env.telegram-operator
    agent_workspace/
      AGENTS.md
      skills/
      automations/
      connectors/

overlays/private/
  machine-rescue/
  connectors/
  hotfixes/
```

`profiles/` and `overlays/private/` are ignored by git. They are for local/private configuration.

Tracked BaseClaw source should provide stable hooks only:

- profile environments
- workspace selection
- allowed paths
- agent provider configuration
- optional speech host configuration
- generic update source configuration

Private overlays should provide the concrete setup:

- private SSH keys and known hosts
- private connector credentials
- project-management automations
- network or machine rescue notes
- local maintenance and hotfix scripts
- supervisor-specific skills
