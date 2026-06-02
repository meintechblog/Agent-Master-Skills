# Agent-Master-Skills

A curated set of self-built **[Claude Code Agent Skills](https://docs.claude.com/en/docs/claude-code/skills)** —
focused, reusable capabilities you can drop into any Claude Code setup. Each skill follows
Anthropic's skill authoring conventions (a `SKILL.md` with `name` + `description` frontmatter,
progressive disclosure via `references/`, and runnable `assets/` / `scripts/`).

## Install

Clone the repo and symlink (or copy) the skills you want into your skills directory:

```bash
git clone https://github.com/<your-org>/Agent-Master-Skills.git
ln -s "$PWD/Agent-Master-Skills/knowledge-base" ~/.claude/skills/knowledge-base
# …repeat for each skill you want, or copy the directory instead of symlinking
```

Claude Code discovers every directory under `~/.claude/skills/` that contains a `SKILL.md`.

## Skills

| Skill | Version | What it does |
|---|---|---|
| [`knowledge-base`](knowledge-base/) | 1.0.0 | Build a RAG-optimized knowledge base on any topic — canonical article schema (Diátaxis), hybrid retrieval (PostgreSQL + pgvector, multilingual-e5, RRF), and a multi-agent audit→transform→ingest playbook. |
| [`webapp-scaffold`](webapp-scaffold/) | 1.0.0 | Scaffold a deployable Next.js 15 web app (black minimal theme) on a native Node + systemd + nginx stack, with an optional pgvector knowledge-base module. |
| [`webapp-auto-updater`](webapp-auto-updater/) | 1.0.0 | Add a safe one-click + nightly auto-update to a self-hosted web app — GitHub-release-driven, blue-green, privileged root installer with healthcheck + rollback. Python/systemd and Node stacks. |
| [`local-speech-service`](local-speech-service/) | 1.0.0 | Install a local STT + TTS HTTP service on Apple Silicon (faster-whisper + Piper-TTS), launchd-managed, no cloud calls, no API keys. |
| [`video-transcribe`](video-transcribe/) | 1.0.0 | Transcribe YouTube videos (or local audio/video) to clean Markdown via captions-first, whisper.cpp fallback. Optimized for tech videos (DE+EN). |
| [`chatgpt-image-restyle`](chatgpt-image-restyle/) | 1.0.0 | Restyle any source image to a consistent visual style via ChatGPT.app — few-shot with your own style references, background mode, auto-verify loop with retry. |
| [`cookidoo-recipe-publisher`](cookidoo-recipe-publisher/) | 1.0.0 | Turn any recipe (URL, plain text, or a photo of a recipe card) into a native-quality Cookidoo "own recipe" with interactive Thermomix command chips. |

Versions follow [Semantic Versioning](https://semver.org/). Each skill is independently versioned.

## Configuration & placeholders

These skills ship with **placeholders**, not real infrastructure. Wherever you see
`<your-org>`, `<lxc-ip>`, `192.0.2.x` / `203.0.113.x` (RFC 5737 documentation addresses),
`__APP_SLUG__`, `__GITHUB_REPO__`, etc., substitute your own values. Secrets always go in a
gitignored `.env.local` (templates provide a `.env.example`) — never commit credentials.

## Contributing

Issues and PRs welcome. Keep each skill self-contained, follow the existing `SKILL.md` structure,
and never commit personal data, secrets, or real host/IP information — use placeholders.

## License

[MIT](LICENSE).
