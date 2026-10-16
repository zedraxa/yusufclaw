# YusufClaw Specialist Roles — Capability Matrix

> Explicit capability matrices governing specialist agent roles, modeled on a capable personal assistant (like Grok Bot) but better.

## 1. Overview and Security Principles

Every specialist role in YusufClaw is constrained by a deterministic **Capability Matrix**:
- **Least Privilege (Progent Monotonic Grants)**: Subagents receive the strict intersection of permissions required for their task.
- **Explicit Tool & MCP Whitelisting**: Any tool or MCP server not explicitly granted is denied fail-closed.
- **Memory Scope Isolation**: Specialist agents can only read and write within authorized scopes; isolation prevents cross-session pollution.
- **External Channel Containment**: Agents are restricted in which channels they can reach. **Crucially: Any external send ALWAYS requires explicit human approval** (`send = always approval`).
- **Rootless Sandbox Execution**: Code execution, git modifications, and cloud operations require rootless container/bubblewrap isolation.
- **ActionCard Approval Gating**: Destructive actions, deployments, submissions, and financial transactions mandate cryptographic ActionCard approval.

## 2. Capability Matrix Comparison Table

| Role | Name | Tier | Risk | Sandbox | Allowed Tools | MCP Servers | Memory (R / W) | Gated Approvals | Send Channels |
| :--- | :--- | :---: | :---: | :---: | :--- | :--- | :--- | :--- | :--- |
| **browser** | Browser & Web Specialist | `strong` | `medium` | Yes (auto) | `browser_navigate`, `browser_click`, `browser_fill`, `browser_screenshot`, `browser_submit`, `web_fetch`, `web_search` | `playwright`, `fetch` | R: session, web_cache<br>W: session | `browser_submit`, `form_submit`, `purchase`, `checkout`, `payment`, `credentials_input` | None (Never sends) |
| **coder** | Coder Specialist | `strong` | `high` | Yes (auto) | `file_read`, `file_write`, `file_list`, `file_delete`, `shell`, `git_status`, `git_diff`, `git_commit`, `git_push`, `pr_create`, `test_runner`, `recall`, `remember` | `filesystem`, `git` | R: session, repo, project, codebase<br>W: session, project | `git_push`, `pr_create`, `shell_destroy`, `sudo` | None (Never sends) |
| **mail** | Mail Specialist | `cheap` | `medium` | No | `mail_read`, `mail_triage`, `mail_summarize`, `mail_draft`, `mail_send`, `recall`, `remember` | `email`, `contacts` | R: inbox, contacts, session, vip_list<br>W: session, triage_notes | `mail_send`, `mail_delete` | `email` |
| **ops** | DevOps & Cloud Specialist | `strong` | `critical` | Yes (auto) | `aws_read`, `aws_describe`, `infra_validate`, `cdk_synth`, `cdk_diff`, `infra_deploy`, `shell` | `aws`, `kubernetes` | R: session, infra, config, architecture<br>W: session, infra_audit | `infra_deploy`, `cdk_deploy`, `aws_write`, `resource_delete`, `iam_modify` | None (Never sends) |
| **orchestrator** | Orchestrator Assistant | `strong` | `medium` | No | `delegate_agent`, `plan_and_execute`, `remember`, `recall`, `revise`, `forget`, `routine_create`, `routine_list`, `routine_delete`, `current_time`, `send_message` | None | R: *<br>W: session, global, routines | `delegate_high_risk`, `spend` | `web`, `telegram`, `cli` |
| **researcher** | Researcher Specialist | `strong` | `low` | No | `web_fetch`, `web_search`, `paper_search`, `arxiv_search`, `file_read`, `file_write`, `recall`, `remember` | `arxiv`, `semanticscholar`, `fetch` | R: session, academic, papers, citations<br>W: session, research_notes | None | None (Never sends) |
| **scheduler** | Scheduler Specialist | `cheap` | `low` | No | `calendar_read`, `calendar_create`, `calendar_delete`, `reminder_set`, `reminder_list`, `current_time`, `remember`, `recall` | `google_calendar`, `caldav` | R: calendar, reminders, session, agenda<br>W: calendar, reminders, session | `calendar_delete`, `calendar_invite_external` | `telegram`, `web` |
| **writer** | Writer Specialist | `strong` | `low` | No | `file_read`, `file_write`, `file_list`, `recall` | `filesystem` | R: session, docs, templates, styleguide<br>W: session | None | None (Never sends) |

## 3. Specialist Role Profiles

### `browser` — Browser & Web Specialist
**Description**: Autonomous web navigation specialist. Traverses web pages, interacts with web apps, fills form fields, and conducts research. Submissions, purchases, and checkouts strictly require human approval.

- **Model Tier**: `strong`
- **Inherent Risk Level**: `medium`
- **Sandbox Isolation**: `Mandatory (auto)`
- **Budget Ceilings**: Max `12288` tokens/turn, `$0.80`/turn, `15` steps/turn.
- **Allowed Tools**: `browser_navigate`, `browser_click`, `browser_fill`, `browser_screenshot`, `browser_submit`, `web_fetch`, `web_search`
- **Allowed MCP Servers**: `playwright`, `fetch`
- **Equipped Skills**: `web-navigation`, `form-automation`, `web-data-extraction`, `screenshot-analysis`
- **Memory Read Scopes**: `session, web_cache`
- **Memory Write Scopes**: `session`
- **Approval-Gated Actions**: `browser_submit`, `form_submit`, `purchase`, `checkout`, `payment`, `credentials_input`
- **Permitted Send Channels**: None (Strictly prohibited from sending)

#### A2A Discovery Card
```json
{
  "name": "browser",
  "description": "Web automation and research specialist (submits and purchases gated by approval)",
  "version": "1.0.0",
  "endpoint": null,
  "capabilities": [
    "web_browsing",
    "form_filling",
    "screenshot_capture",
    "gated_submission"
  ],
  "skills": [
    "web-navigation",
    "form-automation",
    "screenshot-analysis"
  ],
  "input_modes": [
    "text",
    "json"
  ],
  "output_modes": [
    "text",
    "action_card"
  ],
  "author": "YusufClaw",
  "metadata": {}
}
```

### `coder` — Coder Specialist
**Description**: Codex-style software engineering specialist. Edits codebases, executes test suites, performs git operations, and prepares PRs strictly within a sandbox.

- **Model Tier**: `strong`
- **Inherent Risk Level**: `high`
- **Sandbox Isolation**: `Mandatory (auto)`
- **Budget Ceilings**: Max `16384` tokens/turn, `$1.50`/turn, `20` steps/turn.
- **Allowed Tools**: `file_read`, `file_write`, `file_list`, `file_delete`, `shell`, `git_status`, `git_diff`, `git_commit`, `git_push`, `pr_create`, `test_runner`, `recall`, `remember`
- **Allowed MCP Servers**: `filesystem`, `git`
- **Equipped Skills**: `code-refactor`, `test-driven-development`, `git-flow`, `bug-localization`
- **Memory Read Scopes**: `session, repo, project, codebase`
- **Memory Write Scopes**: `session, project`
- **Approval-Gated Actions**: `git_push`, `pr_create`, `shell_destroy`, `sudo`
- **Permitted Send Channels**: None (Strictly prohibited from sending)

#### A2A Discovery Card
```json
{
  "name": "coder",
  "description": "Sandboxed repository intelligence and test execution specialist",
  "version": "1.0.0",
  "endpoint": null,
  "capabilities": [
    "code_editing",
    "testing",
    "git_operations",
    "sandboxed_execution"
  ],
  "skills": [
    "code-refactor",
    "test-driven-development",
    "git-flow"
  ],
  "input_modes": [
    "text",
    "json"
  ],
  "output_modes": [
    "text",
    "action_card"
  ],
  "author": "YusufClaw",
  "metadata": {}
}
```

### `mail` — Mail Specialist
**Description**: Inbox management specialist. Reads, triages, classifies, summarizes, and drafts inbox replies. Sending any email strictly requires explicit human approval.

- **Model Tier**: `cheap`
- **Inherent Risk Level**: `medium`
- **Sandbox Isolation**: `Not required`
- **Budget Ceilings**: Max `8192` tokens/turn, `$0.30`/turn, `8` steps/turn.
- **Allowed Tools**: `mail_read`, `mail_triage`, `mail_summarize`, `mail_draft`, `mail_send`, `recall`, `remember`
- **Allowed MCP Servers**: `email`, `contacts`
- **Equipped Skills**: `inbox-zero`, `email-classification`, `urgent-triage`, `response-drafting`
- **Memory Read Scopes**: `inbox, contacts, session, vip_list`
- **Memory Write Scopes**: `session, triage_notes`
- **Approval-Gated Actions**: `mail_send`, `mail_delete`
- **Permitted Send Channels**: `email`

#### A2A Discovery Card
```json
{
  "name": "mail",
  "description": "Inbox reader, triager, and drafting assistant (sending requires human approval)",
  "version": "1.0.0",
  "endpoint": null,
  "capabilities": [
    "email_triage",
    "inbox_summary",
    "reply_drafting",
    "approval_gated_send"
  ],
  "skills": [
    "inbox-zero",
    "email-classification",
    "response-drafting"
  ],
  "input_modes": [
    "text",
    "json"
  ],
  "output_modes": [
    "text",
    "action_card"
  ],
  "author": "YusufClaw",
  "metadata": {}
}
```

### `ops` — DevOps & Cloud Specialist
**Description**: Cloud infrastructure and DevOps specialist. Operates in validate-only mode by default; infrastructure deployments, modifications, and deletions strictly require human approval.

- **Model Tier**: `strong`
- **Inherent Risk Level**: `critical`
- **Sandbox Isolation**: `Mandatory (auto)`
- **Budget Ceilings**: Max `16384` tokens/turn, `$1.50`/turn, `15` steps/turn.
- **Allowed Tools**: `aws_read`, `aws_describe`, `infra_validate`, `cdk_synth`, `cdk_diff`, `infra_deploy`, `shell`
- **Allowed MCP Servers**: `aws`, `kubernetes`
- **Equipped Skills**: `cdk-synthesizer`, `infra-audit`, `security-compliance`, `cost-optimizer`
- **Memory Read Scopes**: `session, infra, config, architecture`
- **Memory Write Scopes**: `session, infra_audit`
- **Approval-Gated Actions**: `infra_deploy`, `cdk_deploy`, `aws_write`, `resource_delete`, `iam_modify`
- **Permitted Send Channels**: None (Strictly prohibited from sending)

#### A2A Discovery Card
```json
{
  "name": "ops",
  "description": "Cloud infrastructure specialist (validate-only by default, deployments require approval)",
  "version": "1.0.0",
  "endpoint": null,
  "capabilities": [
    "infra_validation",
    "cdk_synthesis",
    "cloud_diff",
    "approval_gated_deploy"
  ],
  "skills": [
    "cdk-synthesizer",
    "infra-audit",
    "cost-optimizer"
  ],
  "input_modes": [
    "text",
    "json"
  ],
  "output_modes": [
    "text",
    "action_card"
  ],
  "author": "YusufClaw",
  "metadata": {}
}
```

### `orchestrator` — Orchestrator Assistant
**Description**: Grok Bot-style master personal assistant. Coordinates routines, plans complex tasks, delegates to specialist subagents, and manages long-term memory.

- **Model Tier**: `strong`
- **Inherent Risk Level**: `medium`
- **Sandbox Isolation**: `Not required`
- **Budget Ceilings**: Max `16384` tokens/turn, `$1.00`/turn, `15` steps/turn.
- **Allowed Tools**: `delegate_agent`, `plan_and_execute`, `remember`, `recall`, `revise`, `forget`, `routine_create`, `routine_list`, `routine_delete`, `current_time`, `send_message`
- **Allowed MCP Servers**: None
- **Equipped Skills**: `task-planning`, `delegation-management`, `routine-automation`, `memory-consolidation`
- **Memory Read Scopes**: `*`
- **Memory Write Scopes**: `session, global, routines`
- **Approval-Gated Actions**: `delegate_high_risk`, `spend`
- **Permitted Send Channels**: `web`, `telegram`, `cli`

#### A2A Discovery Card
```json
{
  "name": "orchestrator",
  "description": "Master coordinator and personal assistant for planning, routines, and delegation",
  "version": "1.0.0",
  "endpoint": null,
  "capabilities": [
    "task_planning",
    "subagent_delegation",
    "routine_management",
    "memory_consolidation"
  ],
  "skills": [
    "task-planning",
    "delegation-management",
    "routine-automation"
  ],
  "input_modes": [
    "text",
    "json"
  ],
  "output_modes": [
    "text",
    "action_card"
  ],
  "author": "YusufClaw",
  "metadata": {}
}
```

### `researcher` — Researcher Specialist
**Description**: Academic and scientific literature research specialist. Deep-dives into arXiv, Semantic Scholar, and web repositories with rigorous citation and evidence verification.

- **Model Tier**: `strong`
- **Inherent Risk Level**: `low`
- **Sandbox Isolation**: `Not required`
- **Budget Ceilings**: Max `16384` tokens/turn, `$1.00`/turn, `15` steps/turn.
- **Allowed Tools**: `web_fetch`, `web_search`, `paper_search`, `arxiv_search`, `file_read`, `file_write`, `recall`, `remember`
- **Allowed MCP Servers**: `arxiv`, `semanticscholar`, `fetch`
- **Equipped Skills**: `literature-review`, `citation-verification`, `comparative-analysis`, `deep-research`
- **Memory Read Scopes**: `session, academic, papers, citations`
- **Memory Write Scopes**: `session, research_notes`
- **Approval-Gated Actions**: None
- **Permitted Send Channels**: None (Strictly prohibited from sending)

#### A2A Discovery Card
```json
{
  "name": "researcher",
  "description": "Scientific and academic deep research specialist with citation verification",
  "version": "1.0.0",
  "endpoint": null,
  "capabilities": [
    "literature_search",
    "paper_analysis",
    "citation_graph",
    "evidence_synthesis"
  ],
  "skills": [
    "literature-review",
    "citation-verification",
    "deep-research"
  ],
  "input_modes": [
    "text",
    "json"
  ],
  "output_modes": [
    "text",
    "action_card"
  ],
  "author": "YusufClaw",
  "metadata": {}
}
```

### `scheduler` — Scheduler Specialist
**Description**: Calendar, reminder, and agenda management specialist. Schedules events, detects time conflicts, configures reminders, and organizes routine agendas.

- **Model Tier**: `cheap`
- **Inherent Risk Level**: `low`
- **Sandbox Isolation**: `Not required`
- **Budget Ceilings**: Max `4096` tokens/turn, `$0.20`/turn, `6` steps/turn.
- **Allowed Tools**: `calendar_read`, `calendar_create`, `calendar_delete`, `reminder_set`, `reminder_list`, `current_time`, `remember`, `recall`
- **Allowed MCP Servers**: `google_calendar`, `caldav`
- **Equipped Skills**: `time-scheduling`, `conflict-resolution`, `reminder-tracking`, `daily-briefing`
- **Memory Read Scopes**: `calendar, reminders, session, agenda`
- **Memory Write Scopes**: `calendar, reminders, session`
- **Approval-Gated Actions**: `calendar_delete`, `calendar_invite_external`
- **Permitted Send Channels**: `telegram`, `web`

#### A2A Discovery Card
```json
{
  "name": "scheduler",
  "description": "Calendar and reminder specialist for personal schedule management",
  "version": "1.0.0",
  "endpoint": null,
  "capabilities": [
    "calendar_management",
    "reminders",
    "time_tracking",
    "conflict_detection"
  ],
  "skills": [
    "time-scheduling",
    "conflict-resolution",
    "reminder-tracking"
  ],
  "input_modes": [
    "text",
    "json"
  ],
  "output_modes": [
    "text",
    "action_card"
  ],
  "author": "YusufClaw",
  "metadata": {}
}
```

### `writer` — Writer Specialist
**Description**: Technical documentation, email drafting, and executive report synthesis specialist. Prepares drafts but never directly sends external communications.

- **Model Tier**: `strong`
- **Inherent Risk Level**: `low`
- **Sandbox Isolation**: `Not required`
- **Budget Ceilings**: Max `8192` tokens/turn, `$0.50`/turn, `8` steps/turn.
- **Allowed Tools**: `file_read`, `file_write`, `file_list`, `recall`
- **Allowed MCP Servers**: `filesystem`
- **Equipped Skills**: `technical-writing`, `executive-summary`, `copy-editing`, `documentation-audit`
- **Memory Read Scopes**: `session, docs, templates, styleguide`
- **Memory Write Scopes**: `session`
- **Approval-Gated Actions**: None
- **Permitted Send Channels**: None (Strictly prohibited from sending)

#### A2A Discovery Card
```json
{
  "name": "writer",
  "description": "Drafting specialist for documents, emails, and reports (never sends directly)",
  "version": "1.0.0",
  "endpoint": null,
  "capabilities": [
    "document_drafting",
    "report_synthesis",
    "content_polishing"
  ],
  "skills": [
    "technical-writing",
    "executive-summary",
    "copy-editing"
  ],
  "input_modes": [
    "text",
    "json"
  ],
  "output_modes": [
    "text"
  ],
  "author": "YusufClaw",
  "metadata": {}
}
```

## 4. Enforcement and Compliance Verification

The capability matrix is enforced by `RolePolicyEnforcer` at the security layer before any tool execution:
1. **Tool Verification**: If a tool is invoked that is not in `allowed_tools`, the policy engine immediately returns `PolicyDecision(kind='deny')`.
2. **MCP Verification**: Unapproved MCP servers are blocked fail-closed.
3. **Memory Isolation**: Write or read operations outside authorized scopes are denied.
4. **Channel Egress**: Roles without send privileges cannot egress messages. For permitted roles, all outbound sends require interactive user confirmation.
5. **High-Impact Actions**: All sensitive actions (e.g. deployments, form submissions, pushes) require human approval cards.
