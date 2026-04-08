# Competitive Analysis: AI Agent Task Management Landscape

**Date:** 2026-03-24
**Status:** Research complete
**Purpose:** Honest assessment of competitors to inform Taskmaster v2 direction

---

## Executive Summary

Five products were analyzed across the AI agent orchestration space. The market is rewarding **breadth and accessibility** (pretty UIs, zero-friction install, multi-agent support, vendor agnosticism) over **depth and rigor** (dependency graphs, review gates, session continuity, lifecycle enforcement). Taskmaster's unique strengths — zero context-switching, session continuity, structured review gates — are real but invisible to users who bounce off the onboarding friction.

The most honest framing from an independent reviewer: *"The fundamental question is whether you want to orchestrate multiple shallow agents or collaborate deeply with one."* Taskmaster chose depth. The market is currently paying for breadth.

---

## 1. cmux — Terminal Multiplexer for AI Agents

**URL:** https://cmux.com / https://github.com/manaflow-ai/cmux
**Backing:** YC (Manaflow)
**Stars:** ~10,000
**License:** AGPL-3.0
**Platform:** macOS only (Apple Silicon + Intel)
**Pricing:** Free, open-source

### What It Is

A native macOS terminal built on Ghostty's `libghostty` rendering engine, purpose-built for running multiple AI coding agents in parallel. Not a general-purpose terminal. Native Swift/AppKit, no Electron.

### The Core Insight They Nailed

The problem isn't "I need a task manager" — it's **"I have 4 agents running and I have no idea which one needs me right now."** cmux treats the terminal as an IPC surface, not just a display. Agents can programmatically set their own status text in the sidebar, trigger notifications, and control pane layout via a Unix socket API.

### What Users Actually Love

- **Notification rings on panes** — colored borders (green=done, yellow=waiting, red=error). Glanceable ambient awareness without switching tabs
- **Cmd+Shift+U** jumps to the most recent notification like an inbox — process agent completions rather than babysitting a grid
- **Sidebar with rich metadata** — git branch, linked PR status, working directory, listening ports, latest notification text. All visible without switching
- **Reads existing Ghostty config** — zero reconfiguration for Ghostty users
- **"Primitives, not a solution" philosophy** — exposes raw building blocks (terminal, browser, notifications, Unix socket, splits) and lets you wire them together
- **Auto-closing panes** when sub-agents finish
- One developer estimated **30% time savings** on environment management — an extra productive hour per day

### Growth Trajectory

- First two weeks: 3,500 stars
- First month: 7,700 stars
- Hit #2 on Hacker News at launch; went viral in Japan
- Promoted by Mitchell Hashimoto (Ghostty/HashiCorp founder)

### Honest Problems

- **486 open issues** skewing toward crashes and basic terminal stability — shipping fast, core reliability not solid yet
- **Stability issues on Intel Macs** — multiple EXC_BAD_ACCESS crashes
- One user called it **"a buggy mess"** early on (dev acknowledged, claimed fixes in v0.57.0)
- **No session restore on relaunch** — layouts are gone after crash/restart
- **Security trade-off**: requires disabling sandbox for Claude Code hooks. Real concern, not FUD
- **tmux scrollback and text selection worse than tmux** (acknowledged publicly)
- One independent reviewer called it **"alpha-grade"**
- macOS-only. No cross-platform support

### Setup Experience

```bash
brew tap manaflow-ai/cmux && brew install --cask cmux
```

Install is easy. But wiring agent hooks requires manual shell configuration at `~/.cmux/hooks/on-task-complete.sh`. One reviewer spent 45 minutes and called it "frustratingly manual."

### Relevance to Taskmaster

**Threat level: Low (complementary).** cmux is a terminal, not a task manager. Could actually be a great host for Taskmaster-managed worktree sessions.

**What Taskmaster should learn:** Taskmaster has zero ambient awareness. When 3 worktree agents are running, you have no way to know which finished without checking each one. cmux's notification architecture solves a problem Taskmaster doesn't even acknowledge exists.

---

## 2. Conductor — Parallel Agent Runner with Git Worktrees

**URL:** https://www.conductor.build / https://docs.conductor.build
**Backing:** YC W25 (Melty Labs), $2.8M seed (Jan 2026)
**Founders:** Charlie Holtz (ex-Replicate growth lead), Jackson de Campos (ex-Point72/Netflix)
**Platform:** macOS Apple Silicon only
**Pricing:** Currently free (paid team tiers planned)
**Notable users:** Individual engineers at Linear, Vercel, Notion, Stripe (not institutional)

### What It Is

A macOS-native desktop app for running multiple Claude Code/Codex agents in parallel, each in isolated git worktrees. Bundles Claude Code and GitHub CLI. UI metaphor is iMessage — each workspace is a conversation thread with diffs per turn, checkpoints, and one-click PR creation.

### The YC Thesis

As AI coding agents get capable enough to do real work unsupervised, the bottleneck shifts from *can the AI write code* to *can a human effectively direct many AIs at once and review their output*. Conductor makes you the conductor of an AI dev team rather than a pair-programmer with one agent.

### What Makes It Genuinely Good

1. **Worktree management is invisible.** Single most praised thing. Press a button → worktree exists → Claude Code running in it. Never think about `git worktree add` syntax, branch naming, or directory structure. Changelog shows this gets refined every release — monorepo handling, `node_modules` copying, nested source directories.

2. **Uses your existing Claude subscription.** No extra billing layer. Claude Pro/Max credentials work directly. Eliminates double-payment friction.

3. **Local-first.** All code stays on your machine. Matters for security-conscious teams.

4. **Unusually good UX for a tool this new.** HN users praised the "functional, visually subtle, and chromatically warm" UI. Native Mac app, keyboard-shortcut-driven (Cmd+D for diff, Cmd+K for command palette, Cmd+P for file picker).

5. **Rapid iteration.** 2-3 releases per week. v0.1.0 (July 2025) → v0.43.0 (early 2026). Features added in direct response to user feedback: GitHub Actions monitoring, Vercel deployment status, Graphite stacked PR support, diff commenting with GitHub sync.

6. **Linear integration.** Inject a Linear issue as context directly into an agent workspace. Issue → agent → PR without manual copy-paste.

### What Users Are Actually Saying

> "Gave this a try and holy shit. This is a new productivity unlock." — Founding Engineer at Supermemory

> "I've been using Conductor every day for a while now and it's the future. The last time I felt this strongly about a developer tool was Vercel and Supabase." — Zach Blume, Stripe engineer

> "A huge amount of my work now is wildly parallelized, in a way I've never had to worry about before."

**The "agent lag" insight**: While one agent thinks (30-120s), you'd otherwise just wait. Conductor makes that dead time productive by running 3-5 agents in parallel.

### The GitHub Permissions Controversy

At launch, Conductor requested full read-write OAuth access to the user's entire GitHub account, including org settings and deploy keys. HN thread (228 upvotes, 115 comments) had significant pushback. Key complaints: "zero disclosure of data practices," each workspace re-clones from GitHub so `.env` and `node_modules` don't carry over. Founder engaged directly, committed to GitHub App auth for fine-grained permissions. Mostly resolved but damaged early trust.

### Honest Problems

- **macOS Apple Silicon only** — single biggest exclusion. Windows/Linux "hopefully soon-ish via WSL" has been on the roadmap for months
- **No background execution** — agents only run while the app is open
- **No team features yet** — can't see colleague's workspaces
- **Dependency/env file leakage** — worktrees don't automatically get `.env` or `node_modules`
- **Early-stage bugs** — crashes in novel situations, corrupted terminal output, undo doesn't work mid-composition

### Competitive Risk

Every major player is building what Conductor does. Cursor Cloud has agents in dedicated VMs with video recording. Windsurf has parallel sessions. Claude Code has an experimental `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` flag. These are better-funded and embedded in existing workflows.

### Product Direction (from changelog)

Moving from "Claude Code with worktrees" toward:
- Model agnosticism (GPT-5/Codex support added)
- Full PR lifecycle in-app
- Team collaboration (script sharing, `.context` directories)
- Deployment awareness (GitHub Actions, Vercel monitoring)
- Migration guides from Cursor — direct statement of competitive intent

### Relevance to Taskmaster

**Threat level: Medium.** Overlaps on worktree isolation but operates at a different layer. Conductor is a GUI shell; Taskmaster is a planning brain.

**What Taskmaster should learn:** Taskmaster's worktree creation is verbose — emits git commands for Claude to run, records branch/worktree fields, requires skill orchestration. Conductor made this a single invisible button press. **The gap isn't features, it's friction.** Also: Conductor added Linear integration, GitHub Actions monitoring, Vercel deployment status — it's becoming the IDE for AI-first dev, not just a worktree wrapper. Taskmaster has no integrations with external services.

---

## 3. VibeKanban — Plan/Prompt/Review Platform

**URL:** https://vibekanban.com / https://github.com/BloopAI/vibe-kanban
**Backing:** YC (BloopAI, London), $7.43M raised
**Stars:** 23,700
**Forks:** 2,300
**Contributors:** 66
**npm downloads:** ~10,645/week
**License:** Apache 2.0
**Stack:** Rust (49.7% backend) + TypeScript/React (46.6% frontend)
**Pricing:** Free ($0) / Pro ($30/user/month) / Enterprise (custom)

### What It Is

An open-source orchestration layer that sits on top of AI coding agents. Core thesis: coding agents now run faster than humans can supervise them, so the bottleneck has shifted to planning and review. VibeKanban optimizes those two human-side phases.

### The Numbers — What's Real vs Marketing

- **23.7k GitHub stars** — real, cross-verifiable
- **30k active users / 100k PRs** — stated on homepage, not independently verified
- **HN launch** got 195 points and 132 comments — legitimate organic traction
- **Addy Osmani and Luke Harries (ElevenLabs) endorsements** — real
- The **"10X productivity"** framing is marketing. An honest independent review estimates **2-3X for typical developers** on parallelizable tasks

### What Drives Adoption

Three compounding factors:

1. **Timing.** Launched directly into the 2025 agentic coding wave. Developers running Claude Code had no GUI — VibeKanban gave them one in a single `npx` command.
2. **Git worktree isolation.** Each task gets its own worktree. Solves the most common "parallel agents" failure mode.
3. **Breadth of agent support.** Claude Code, Codex, Gemini CLI, GitHub Copilot, Amp, Cursor, OpenCode, Aider, Windsurf — 10+ agents. Immediately useful regardless of agent choice.

### The Plan → Prompt → Review Loop: How It Actually Feels

**Plan phase:** Create Kanban cards with task descriptions. Drag to "In Progress" to trigger execution. Adds friction vs direct conversation — you're writing a card description instead of just typing to Claude. One reviewer called this "excessive steps" for solo developers.

**Prompt/execution phase:** Agent runs in its own worktree branch. Real-time WebSocket streaming of reasoning, commands, file operations, MCP server calls. Average task completion 2-5 minutes. Multiple "attempts" on the same task supported — compare implementations from different models side by side.

**Review phase:** Line-by-line diff viewer with inline commenting. Comments go directly back to the agent → agent revises. One-click merge when approved. **The review discipline this enforces is cited by multiple reviewers as the most underrated feature.**

**Overall:** For parallel independent tasks, genuinely removes cognitive load. For tightly-coupled sequential work, the overhead of the Kanban layer is noticeable.

### Built-in Browser Testing (More Substantial Than It Sounds)

- **Eruda-powered DevTools**: Console, DOM inspector, network monitor, localStorage viewer, source viewer — all embedded
- **Device emulation**: Desktop, Mobile (390x844), Responsive (draggable)
- **Click-to-component inspection**: Hover, highlight, click → DOM selector + component hierarchy + source file location sent directly to the agent as context. Works with React, Vue, Svelte, Astro, plain HTML
- **Dev server integration**: Configure once, start from workspace, logs stream in real time

**What doesn't work:** Autonomous QA is not achieved. Without explicit AGENTS.md instructions, agents ignore browser tools. One developer spent $103 on a single thread when context bloated with screenshots and logs.

### Vendor Agnosticism: Real or Marketing?

**Mostly real.** The Rust backend has an `Executor` trait that genuinely abstracts over agent implementations. Adding a new agent is a defined interface. However, Claude Code integration is most polished. UX quality varies per agent. Windows support is "rough."

### Biggest Real Complaints

1. **Database lock errors** — running tasks get stuck indefinitely, blocking merge. "database is locked" appears repeatedly. No recovery path from UI (issue #1718)
2. **WebSocket disconnections** — session data loss
3. **Performance at scale** — MacBook slowdowns after 4 concurrent agents. 4.7MB JS bundle, 7-10 second load times
4. **`--dangerously-skip-permissions` by default** — security concern
5. **Privacy defaults** — analytics were opt-out at launch (fixed within an hour of HN callout, but trust damaged)
6. **Cloud version deprecation warning for local projects** — frustrated self-hosted users
7. **Semantic merge conflicts remain unsolved** — worktrees solve file-level conflicts but not two agents rewriting auth logic differently

### The Key Insight

From an independent reviewer:

> "The fundamental question is whether you want to orchestrate multiple shallow agents or collaborate deeply with one. VibeKanban optimizes for the former. Claude Code direct optimizes for the latter. Neither is wrong — they're different workflows."

### Relevance to Taskmaster

**Threat level: High.** Most feature-overlapping competitor. Covers planning, execution isolation, and review.

**What Taskmaster should learn:**
- **Zero-friction onboarding.** `npx vibe-kanban` gets you running in 30 seconds. Taskmaster requires plugin install + uv + understanding skills/MCP — 10+ minutes minimum.
- **Multi-agent support.** 10+ agents. Taskmaster is Claude Code only. Vendor lock-in is a real limitation.
- **Visual review workflow.** Line-by-line diff viewer with inline commenting that feeds back to the agent. Taskmaster delegates review to Claude's inline conversation.
- **Browser testing.** Built-in preview with DevTools. Taskmaster has nothing comparable.

---

## 4. Aperant — Autonomous Multi-Agent Framework

**URL:** https://aperant.com / https://github.com/AndyMik90/Aperant
**Stars:** 13,500
**Forks:** 1,800
**License:** AGPL-3.0 (commercial license available)
**Stack:** Electron + TypeScript + React 19
**Platform:** Windows, macOS, Linux
**Pricing:** Free, open-source
**Latest stable:** v2.7.6 (Feb 2026). v2.8.0-beta is currently unstable.

### What It Is

An Electron desktop app that wraps Claude Code CLI and orchestrates a pipeline of 25+ specialized agents against an isolated git worktree. Formerly named "Auto Claude."

**The core pipeline:**
```
User task → Spec (Researcher → Writer → Critic) → Planner → Coder (parallel) → QA Reviewer → QA Fixer → Human review → Merge
```

### The 13.5k Stars — What That Actually Signals

The stars reflect genuine interest in the *category* (autonomous local AI dev orchestration), not necessarily satisfaction with the product. The project filled a real gap when it launched. But: the community is mostly setup questions and feature requests, not "I shipped X with this." The one concrete testimonial on the website is a non-technical founder building a Norwegian outdoor activity platform — legitimate but modest.

### Why It Was Renamed (Most Consequential Recent Event)

Two drivers:
1. **Anthropic's OAuth policy change (Feb 2026)** restricted OAuth tokens from Pro/Max accounts to Claude Code and Claude.ai only. Using them in third-party tools became a ToS violation. This was existential — most users authenticated via OAuth against Pro subscriptions.
2. **Scope ambition** — repositioning from "utility layer on Claude Code" to "AI software lifecycle platform."

**Practical consequence:** Users now pushed toward API key auth (pay-per-token) instead of subscription-based OAuth. Significantly changes the cost model. Transition still creating friction (auth failures, token revocation loops).

### What the Autonomous Workflow Actually Feels Like

**The good path:** For greenfield or well-scoped tasks on medium codebases, the pipeline genuinely works. Write a task description, approve the spec, come back to a PR in an isolated worktree with QA results.

**The friction:**
- Planning phase is **slow and token-hungry**. 22+ minutes for trivial single-line changes because spec pipeline runs regardless of task size
- **Feedback loop on plans is broken.** Even with "require human review" enabled, the planner proceeds to coding before the user can correct the spec (Discussion #1563, multiple users confirming)
- Task descriptions must be **extremely specific.** "Fix bugs" or "add feature" produces garbage
- **Overnight builds have OOM issues** and orphaned agent processes (v2.7.6 changelog explicitly fixes this)
- **Windows**: persistent CLI detection failures on non-C: drives, process leaks requiring Task Manager

### The Self-Validating QA Pipeline — Honest Assessment

Architecture is credible: QA Reviewer checks acceptance criteria, QA Fixer attempts resolution, configurable approval gates.

**In practice, reliability is limited:**
1. **Validates against the spec, not reality.** If the spec was wrong, QA passes silently. Not independent reasoning about correctness.
2. **The ultrathink token limit bug (issue #1212)** — QA used invalid token budget (65,536 vs API's 64,000 limit), making the entire pipeline fail silently for "anything significant." 8 users confirmed before 2-day patch. Shows QA pipeline itself wasn't tested against real API constraints.
3. **Data integrity failures** — git commit failures during task processing mean work can be lost mid-pipeline (issues #1938, #1928). Most dangerous failure mode for an autonomous tool.

### Memory Layer (Graphiti)

Uses a graph-based semantic memory via Python MCP sidecar (initially FalkorDB, migrated to LadybugDB). Optional, requires configuration.

**Is it useful?** No community evidence of users raving about it. Most-upvoted requests are about alternative LLM providers and server deployment, not memory features. For small-to-medium projects, CLAUDE.md files serve a similar purpose. Adds operational complexity for unclear benefit.

### 12 Parallel Agents — Marketing vs Reality

Architecture supports 12. **Most users run 1-3.**

Why:
- **Cost:** 12 simultaneous Opus-class agents at API pricing is extremely expensive
- **Rate limits:** 12 against one account hits limits immediately. Multi-profile helps but 12 profiles isn't realistic
- **Task decomposition:** The planner needs 12+ truly independent subtasks. Most features don't decompose that cleanly
- **Machine resources:** 12 terminal processes is heavy. Changelog explicitly fixes "async parallel worktree listing prevents UI freezing" — even modest parallelism was causing lockups

### Queue System v2 (Actually Clever)

- **Smart prioritization with auto-promotion** — reorder based on urgency/dependencies, not strict FIFO
- **Intelligent rate limit handling** — pauses and auto-resumes when limits reset
- **Multi-profile account rotation** — automatic token refresh and rate limit recovery by switching profiles

v1 was FIFO with manual intervention. v2 is meaningfully more robust for "set it and leave it."

### Most Common Complaints (Ranked by Frequency)

1. Auth/OAuth failures and token revocation loops (Anthropic policy change)
2. Module not found errors in beta builds (v2.8.0-beta unusable)
3. Windows-specific failures
4. Token consumption for small tasks (spec pipeline runs unconditionally)
5. Planning phase can't be interrupted or corrected
6. Rate limit interruptions during multi-step tasks
7. Data loss on merge/commit failures
8. MCP tool connectivity failures

### Relevance to Taskmaster

**Threat level: High (different axis).** Aperant targets less human involvement. Taskmaster targets disciplined human-AI collaboration. Different philosophies, overlapping users.

**What Taskmaster should learn:**
- There's genuine demand for a **"fire and forget" mode** that Taskmaster completely lacks. The v2 "spike" concept addresses this partially.
- **Queue System v2's rate limit handling and account rotation** is clever infrastructure that would benefit any multi-agent system.
- Aperant's planning-phase friction (22 min for trivial changes) validates Taskmaster v2's concern about ceremony overhead — but Aperant shows the opposite extreme is also bad.
- **The spec→plan→code pipeline** is the autonomous future. Taskmaster has review gates but no autonomous planning or QA pipeline.

---

## 5. Maestri — Infinite Canvas Agent Workspace

**URL:** https://www.themaestri.app/en
**Creator:** Rafael Thayto (solo developer, senior engineer at OutlitAI)
**Platform:** macOS 26.2+ Apple Silicon only
**Pricing:** Free (1 workspace) / $18 one-time lifetime (unlimited workspaces)
**Version:** 0.15.8 (launched March 18, 2026 — one week old at time of analysis)
**Stars:** N/A (no public repo found)

### What It Is

A native Swift/SwiftUI app with a custom Metal-powered canvas engine built from scratch. Infinite 2D spatial canvas where every running agent lives simultaneously.

### What Makes the Infinite Canvas Approach Actually Different

The value is **cognitive load reduction, not technical capability**. Tabs and splits already let you run multiple agents. What they don't do:

- **Persistent spatial context.** Frontend agent on the left, backend on the right, architecture sketch in the middle. After context-switching, you pan back and your brain reconstructs state from spatial layout — the desk metaphor.
- **Zoom as workflow.** Zoom out to see all agents and status, zoom in on what needs attention. Tabs have no concept of "distance."
- **Sketch alongside work.** Draw arrows between components while an agent works on the thing the arrow points to.
- **Agent-written notes.** Connect a terminal to a sticky note; the agent populates it with summaries automatically.

### Ombro — On-Device AI Monitor

Uses Apple Foundation Models (~3B parameter, quantized for Neural Engine). Operates entirely locally — no API keys, no cloud calls. Watches agent terminal output, surfaces floating notifications (outside the app) with task summaries and suggested next actions.

**Honest limitations:** Apple Foundation Models are not GPT-4-class. Ombro does summarization and short next-step suggestions — a reasonable fit for a small on-device model. Will miss subtle logic errors or partial successes that look complete. **It's a smart notification system, not an autonomous orchestrator.**

### Inter-Agent Messaging — What It Actually Is

Almost certainly stdout-to-stdin piping between terminal processes, not a structured message bus. Draw a connection on the canvas → agent A's output pipes to agent B's input. Essentially a visual interface for Unix pipe operators. Whether this works reliably with Claude Code's interactive prompting is unclear. The 0.15.8 changelog includes "inter-agent communication stability improvements" — still being stabilized.

### $18 Lifetime Pricing Assessment

Sustainable as a side project / lifestyle business (no cloud backend, no telemetry, zero marginal cost per user). Not sustainable if users expect funded-team development pace. Solo developer shipping 5 releases in 6 days is healthy but depends on sustained motivation.

### Actual User Base

Unknown and unverifiable. Zero telemetry by design. Launch tweet: 3,433 views, 18 retweets, 10 likes. Very small launch. No HN "Show HN" found. App is one week old.

### Honest Problems

- **macOS 26.2+ requirement** — requires beta OS. Hard filter.
- **Apple Silicon only** — excludes Intel Macs
- International keyboard layouts broken until 0.15.8
- Terminal crashes and Cmd+Z crashes (fixed in recent patches)
- **Spatial layout doesn't solve the fundamental multi-agent coordination problem.** Race conditions, file ownership conflicts, and validation bottlenecks exist regardless of how pretty the canvas looks.

### Relevance to Taskmaster

**Threat level: Low.** Too early, too niche (beta macOS + Apple Silicon), tiny user base. The spatial concept is interesting but unproven.

**What Taskmaster should learn:** Ombro's approach — using a **cheap local model to monitor agent activity** and surface summaries — is an interesting pattern. Taskmaster's session continuity (PROGRESS.md) solves cross-conversation amnesia but doesn't help within a session when multiple agents are active.

---

## Comparative Matrix

| Capability | cmux | Conductor | VibeKanban | Aperant | **Taskmaster** |
|---|---|---|---|---|---|
| **Time to first value** | `brew install` (2 min) | Download app (1 min) | `npx vibe-kanban` (30 sec) | Download + auth (5 min) | Plugin + uv + skills (10+ min) |
| **GitHub stars** | ~10k | N/A (closed) | 23.7k | 13.5k | 0 (private) |
| **Visual experience** | Native terminal + sidebar | Polished native Mac app | Web kanban + diff viewer | Electron desktop | CLI text + basic HTML kanban |
| **Multi-agent awareness** | Notification rings, inbox | Dashboard, per-agent view | Kanban, WebSocket streaming | Kanban + 12 terminals | **None** (check each manually) |
| **Agent support** | Any CLI agent | Claude/Codex | 10+ agents | Claude only | Claude Code only |
| **Autonomy level** | None (terminal) | Low (human prompts) | Medium (plan→execute→review) | High (full pipeline) | Low (human drives) |
| **Worktree isolation** | No | Auto (invisible) | Auto | Auto | Auto (but verbose) |
| **Review workflow** | No | Diff viewer + PR | Diff + comments + browser | Self-validating QA | 3-gate review (CLI) |
| **Session continuity** | No | No | No | Memory layer (graph DB) | **Yes** (PROGRESS.md + context) |
| **Dependency tracking** | No | No | Parent/sub-issues | Queue prioritization | **Full DAG + cycle detection** |
| **TODO bridging** | No | No | No | No | **Yes** (check-todos) |
| **Test auto-detection** | No | No | Built-in browser | QA pipeline | **Multi-language** |
| **Platform** | macOS | macOS (Apple Silicon) | Web (any OS) | Win/Mac/Linux | Anywhere Claude Code runs |
| **Pricing** | Free/OSS | Free (for now) | $0-$30/mo | Free/OSS (AGPL) | Free (plugin) |

---

## What They All Have That Taskmaster Doesn't

1. **Visual feedback loops.** Every competitor has real-time visual awareness of agent state. Taskmaster's kanban viewer is a static HTML page requiring manual refresh.

2. **Near-zero onboarding friction.** `npx`, `brew install`, or download an app. Taskmaster requires understanding the Claude Code plugin system, installing `uv`, and learning skill commands.

3. **Multi-agent monitoring.** When 3 agents are working, you can see all of them. Taskmaster gives you one conversation at a time.

4. **Broad agent support.** VibeKanban supports 10+ agents. cmux supports any CLI tool. Taskmaster is Claude Code only.

5. **External service integrations.** Conductor has Linear, GitHub Actions, Vercel. VibeKanban has GitHub OAuth. Aperant has GitHub/GitLab/Linear. Taskmaster has zero external integrations.

## What Taskmaster Has That None of Them Do

1. **Session continuity across conversations.** PROGRESS.md changelog + context block + start-session briefing. No competitor solves Claude's amnesia problem. This is genuinely unique and valuable.

2. **Zero context switching.** Everything happens in natural language in the same Claude conversation. No app switching, no browser tabs. (But this is also why it has no visual feedback.)

3. **Dependency DAG with circular dependency detection.** Real graph-based task ordering with DFS cycle detection. Competitors do flat lists or simple parent/child.

4. **TODO ↔ backlog bridging.** check-todos connects inline code comments to the task system. Nobody else does this.

5. **Structured review gate with multi-language test detection.** Auto-detects pytest, cargo, go, npm, dotnet without configuration. More rigorous than any competitor's review workflow.

6. **Single portable file.** Entire project state in one YAML file. No database, no server, no account. Can be committed to git for team visibility.

---

## Strategic Implications for Taskmaster v2

### The Market Position

Taskmaster occupies a unique niche: **the planning brain that lives inside the AI agent itself.** Every competitor is an external tool that wraps around agents. Taskmaster is the only one that makes the agent self-managing — Claude manages the backlog, tracks sessions, and enforces quality gates from within the same conversation.

### The Biggest Risk

The **visual gap**. All competitors bet heavily on polished GUIs. Taskmaster's CLI-first approach is its strength (zero context-switch) but also its weakness (harder to sell, harder to demo, harder to onboard). Stars/adoption correlate with visual impressiveness, not feature rigor.

### The Biggest Opportunity

**Integration with visual tools.** Taskmaster as the brains + Conductor/cmux/Maestri as the eyes. The YAML-file-as-API-surface makes this feasible. A Conductor plugin that reads backlog.yaml could combine Taskmaster's planning depth with Conductor's visual execution layer.

### Concrete Takeaways for v2

1. **Reduce ceremony overhead** (already identified in design-v2.md as P1). The spike concept directly addresses the "22 minutes for a one-line change" problem that Aperant also suffers from.

2. **Consider a lightweight web dashboard with auto-refresh.** The current static kanban viewer is a liability when competitors have real-time WebSocket streaming. Even a simple polling mechanism would help.

3. **The onboarding story needs work.** `npx vibe-kanban` vs "install plugin, install uv, understand MCP, learn skills." Consider a `npx taskmaster-init` or similar zero-friction entry point.

4. **Multi-agent awareness is the gap nobody in the CLI space is filling.** If Taskmaster could surface which worktree agents finished/need attention (even just via terminal notifications or a polling dashboard), it would address the #1 complaint across all competitors' user bases.

5. **Vendor agnosticism is a real competitive advantage that Taskmaster lacks.** VibeKanban's 23.7k stars are partly explained by supporting every agent. Even if Taskmaster remains Claude Code-native, the backlog.yaml format could be consumed by other tools.

6. **The "orchestrate shallow vs. collaborate deep" framing is the strategic choice.** Taskmaster is deep collaboration. The market is buying shallow orchestration. The v2 spike concept is the right bridge — offering both modes in one tool.

---

## Sources

### cmux
- [GitHub - manaflow-ai/cmux](https://github.com/manaflow-ai/cmux)
- [Show HN discussion](https://news.ycombinator.com/item?id=47079718)
- [cmux Review 2026 - vibecoding.app](https://vibecoding.app/blog/cmux-review)
- [Better Stack Guide](https://betterstack.com/community/guides/ai/cmux-terminal/)
- [cmux vs tmux - soloterm.com](https://soloterm.com/cmux-vs-tmux)

### Conductor
- [YC Company Page](https://www.ycombinator.com/companies/conductor)
- [Launch YC](https://www.ycombinator.com/launches/OHk-conductor-run-a-bunch-of-claude-codes-in-parallel)
- [Show HN discussion (228 pts, 115 comments)](https://news.ycombinator.com/item?id=44594584)
- [Docs](https://docs.conductor.build)
- [Changelog](https://www.conductor.build/changelog)
- [The New Stack Review](https://thenewstack.io/a-hands-on-review-of-conductor-an-ai-parallel-runner-app/)

### VibeKanban
- [GitHub - BloopAI/vibe-kanban](https://github.com/BloopAI/vibe-kanban)
- [Show HN discussion](https://news.ycombinator.com/item?id=44533004)
- [Honest Review - solvedbycode.ai](https://solvedbycode.ai/blog/vibe-kanban-honest-review)
- [Architecture - VirtusLab](https://virtuslab.com/blog/ai/vibe-kanban)
- [Browser Testing Docs](https://vibekanban.com/docs/workspaces/preview)

### Aperant
- [GitHub - AndyMik90/Aperant](https://github.com/AndyMik90/Aperant)
- [aperant.com](https://aperant.com/)
- [Project Evolution - Zread](https://zread.ai/AndyMik90/Aperant/7-from-auto-claude-to-aperant-project-evolution)
- [Issue #1212: Ultrathink bug](https://github.com/AndyMik90/Aperant/issues/1212)
- [Discussion #1563: Plan feedback](https://github.com/AndyMik90/Aperant/discussions/1563)
- [Morph LLM: 15 AI Coding Agents tested](https://www.morphllm.com/ai-coding-agent)

### Maestri
- [themaestri.app](https://www.themaestri.app/en)
- [Launch tweet](https://x.com/thayto_dev/status/2033927487994867765)
- [HN: Agent orchestration discussion](https://news.ycombinator.com/item?id=46993479)
- [Apple Foundation Models - WWDC25](https://developer.apple.com/videos/play/wwdc2025/286/)
