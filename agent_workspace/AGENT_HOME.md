# Agent Workspace

This folder is the default home for the Telegram Codex assistant when the user has not selected another workspace.

Factory settings:

- Keep the system lightweight and understandable.
- Use this folder for notes, prototypes, routines, and generated work when the user does not name another path.
- Prefer learning or adding capabilities only when a real user goal requires them.
- Treat the Telegram conversation, speech interface, Codex access, and message journal as the baseline body of the assistant.
- Do not turn the assistant into a large framework before the need is clear.

Workspace map:

- `agent/`: the assistant's inner equipment.
- `agent/skills/`: capabilities the assistant learns or installs, including MCP servers, CLI tools, reusable scripts, integration notes, and local skill instructions.
- `agent/memory/`: durable user preferences, summaries, facts, relationship context, and experiments with persistent memory systems.
- `agent/senses/`: inputs and perception adapters, such as speech transcription, screenshots, browser observation, file watchers, or future sensors.
- `work/`: things the assistant is doing or helping the user make.
- `work/prototypes/notes/`: early ideas, things to remember for later, rough plans, and possible project seeds.
- `work/prototypes/builds/`: quick experiments, mockups, throwaway implementations, and proof-of-concept work.
- `work/projects/`: real user-facing work, grouped by project. Each project can contain its own plan, notes, source, assets, and decisions.
- `work/routines/`: recurring tasks, monitors, reminders, scheduled checks, and background workflows.

Guidance:

- Put new material in the smallest folder that honestly fits.
- When the user has a loose idea, write it down under `work/prototypes/notes/`.
- When the idea needs exploration, build or test it under `work/prototypes/builds/`.
- When a prototype becomes real work, create a project folder under `work/projects/` and keep its plan beside the project.
- Keep assistant-internal plans under `agent/` only when they are about the assistant's own capabilities.
- Do not install or invent permanent capabilities without a user goal or an explicit experiment.
