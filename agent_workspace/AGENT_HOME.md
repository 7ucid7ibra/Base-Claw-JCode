# Agent Workspace

This folder is the default home for the Telegram Codex assistant when the user has not selected another workspace.

Factory settings:

- Keep the system lightweight and understandable.
- Use this folder for notes, prototypes, routines, and generated work when the user does not name another path.
- Prefer learning or adding capabilities only when a real user goal requires them.
- Treat the Telegram conversation, speech interface, Codex access, and message journal as the baseline body of the assistant.
- Do not turn the assistant into a large framework before the need is clear.

Local folder map:

- `skills/`: local capabilities the assistant can use or maintain, including MCP notes, CLI wrappers, reusable scripts, and skill instructions.
- `automations/`: repeatable workflows, scheduled checks, monitors, reminders, and other routines.
- `projects/`: real user work, such as apps, websites, research folders, prototypes that became serious, and project-specific plans.
- `slash_commands/`: local command definitions, command templates, and notes for user-defined shortcuts.
- `notes/`: loose notes, rough plans, observations, and ideas that do not belong to a specific project yet.
- `scratch/`: temporary tests and throwaway experiments.
- `artifacts/`: generated files meant to be inspected, shared, or handed back to the user.
- `uploads/`: local attachment intake and files saved from conversations.

Guidance:

- Put new material in the smallest folder that honestly fits.
- When the user has a loose idea, write it down under `notes/`.
- When the idea needs exploration, test it under `scratch/`.
- When a prototype becomes real work, create a project folder under `projects/` and keep its plan beside the project.
- Keep reusable assistant behavior under `skills/`, `automations/`, or `slash_commands/` instead of editing BaseClaw core.
- Do not install or invent permanent capabilities without a user goal or an explicit experiment.

Git policy:

- BaseClaw tracks this `AGENT_HOME.md` file as the starter note.
- The other folders in this workspace are local runtime/private space and are ignored by git.
- Move something out of this workspace only when it is meant to become public source, documentation, or a shareable example.
