# Agent instructions

Read [README.md](README.md) before changing or running this project.

- Keep the interface read only. HTTP serves the board and task JSON; action buttons copy prompts.
- Run locally with `python3 server.py --no-browser`. No package installation is required for this project.
- Keep `index.html` limited to fictional demo data. Never commit local Beads databases, exports, issue descriptions, private paths, or credentials.
- Use `bd --readonly` for live reads. Respect each user's local Beads setup and project instructions.
- Discover projects only from the private registry, explicit `--project` paths, or ignored `projects.local.txt`. Do not scan the user's home directory or commit their path list.
