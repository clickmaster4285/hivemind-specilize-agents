
# MulteAgent: Complete IT Specialist Feature Set

For your MulteAgent platform, I recommend building the IT Specialist as a full IT Operations, Infrastructure, DevOps, Networking, and Security AI agent that can investigate real systems, diagnose problems using evidence, recommend solutions, and safely execute approved actions.

The goal is not just an AI chatbot that explains Linux commands. It should be an agent that can understand your infrastructure, connect to authorized servers, use tools, analyze results, maintain incident history, and help resolve problems.

Your existing project already has a foundation: a local-first multi-agent platform, semantic routing, PostgreSQL and pgvector knowledge retrieval, SSH session management, `network_scan`, `security_check`, `server_audit`, and a tool-calling loop. The following roadmap builds on that foundation rather than replacing it.&#x20;

Pasted text(20261021-061440).txt

---

## Build status — 2026-10-21

This spec's own priority ladder (§17) is the plan we are following. Current state:

| Priority | What it is | Status |
|---|---|---|
| **P0** | Validate the SSH layer / `server_audit` | ✅ built — read-only Linux audit over SSH (`src/linux_audit.py`); session-only logins (`src/server_vault.py`). Live-test pending. |
| **P1** | Read-only remote **diagnosis** | ✅ built — `diagnose_server` (`src/server_ops.py`): the agent runs its own read-only commands in a loop; a deterministic allow-list classifier (`classify_command`) is the gate; streaming status, output caps, Stop, 64-case security test. |
| **P2** | **Approval + audit** framework | ✅ built — `operate_server`: per-server trust mode (audit / confirm / trusted), a **Run/Cancel** confirm card for each change (`live.wait_confirm` + `POST /api/jobs/{id}/confirm`), a destructive-command deny-list, and an audit log (`app.command_log`, `src/command_audit.py`). |
| **P3** | Incident intelligence, runbooks, monitoring, alerts, richer inventory | ⬜ not started |
| **P4** | Windows (WinRM), DB ops, deployment, backup, autonomous actions | ⬜ not started |

Also built earlier and used by the IT specialist: `network_scan` (device list), `security_check`
(ports / services / firewall), `ping_host` (one device up/down). Decisions and build notes:
`doc/SERVER_ACCESS_DECISION.md`, `doc/SECURITY_TOOLS_DECISION.md`.

**Next:** P3 — pick the first item (structured diagnosis workflows, incident history, knowledge-backed
runbooks, monitoring/alerts, or a richer server inventory dashboard).

---

## 1. The complete capability map

Infrastructure

Linux, Windows, servers, SSH, services, resources

Networking

Connectivity, DNS, ports, routes, firewalls

Databases

PostgreSQL, MongoDB, backups, query diagnostics

Development & DevOps

Git, deployments, APIs, Nginx, CI/CD

Security

Audits, permissions, vulnerabilities, incident response

Automation

Runbooks, approvals, scheduled jobs, recovery

Intelligence

Evidence, RAG, incident memory, root-cause analysis

Operations Center

Live status, alerts, jobs, audit history, reports

## 2. Server and operating-system management

The agent should understand and investigate both Linux and Windows environments, with the available operations determined by the permissions and connectors configured for each host.

### Linux capabilities

- Connect to registered servers through authorized SSH sessions.
- Identify hostname, operating system, kernel, uptime, CPU architecture, and installed resources.
- Monitor CPU, RAM, swap, disk usage, disk inodes, and I/O.
- Inspect running processes and resource-intensive applications.
- Check `systemd` services, failed units, service dependencies, and restart history.
- Analyze `journalctl`, application logs, authentication logs, and kernel messages.
- Inspect listening ports, active connections, and network interfaces.
- Diagnose full disks, memory pressure, CPU spikes, failed services, and boot problems.
- Inspect selected configuration files and compare them with known-good configurations.
- Check scheduled jobs, systemd timers, storage mounts, and filesystem health.
- Review package versions, updates, and pending reboots.
- Investigate SSH authentication and connection failures.

### Windows capabilities

- Connect through WinRM or an approved Windows management connector.
- Inspect Windows services, processes, memory, CPU, and disk capacity.
- Read Windows Event Logs and relevant application logs.
- Check networking, DNS, listening ports, firewall rules, and routes.
- Inspect IIS, scheduled tasks, Windows updates, and service failures.
- Check PowerShell configuration and Windows Server health.
- Support RDP access workflows, such as generating an `.rdp` launch file without handling user passwords.

Key requirement: Linux SSH access and Windows management are different integrations. Do not advertise Windows remote execution until its connector and authorization system have been implemented and tested.

## 3. Network discovery and troubleshooting

The agent should be able to answer questions such as: “Why can the frontend not reach the backend?” or “Which service is listening on port 8100?”

Network discovery

Scan explicitly authorized IP ranges, identify reachable hosts, check approved TCP ports, and produce an inventory.

Connectivity diagnostics

Run ping where permitted, TCP connection tests, traceroute, route inspection, and interface checks.

DNS diagnostics

Investigate DNS resolution, records, search domains, and hostname mismatches.

Port analysis

Identify listening ports and map them to processes and services where permissions allow.

Firewall analysis

Inspect UFW, nftables, iptables, Windows Firewall, and relevant cloud security rules.

Web and API connectivity

Test HTTP/HTTPS endpoints, response codes, TLS certificates, reverse proxies, and upstream reachability.

Network performance

Investigate packet loss, latency, connection saturation, MTU problems, and bandwidth bottlenecks.

Topology and inventory

Build a documented view of approved servers, subnets, services, and dependencies.

Security boundary: scans must be limited to authorized networks. The agent should never expand scans into unrelated networks on its own.

## 4. Database administration

The agent should diagnose database problems using dedicated, permission-limited database connectors rather than relying exclusively on shell commands.

| Feature     | What the agent should do                                                                                                          |
| ----------- | --------------------------------------------------------------------------------------------------------------------------------- |
| PostgreSQL  | Check connectivity, database size, table sizes, connections, locks, long-running queries, and replication status where configured |
| MongoDB     | Inspect database and collection sizes, connection health, indexes, slow operations, and replica-set health                        |
| Performance | Identify expensive queries, missing-index candidates, connection saturation, and resource bottlenecks                             |
| Backups     | Verify backup schedules, recent backup results, retention, and restore-test status                                                |
| Capacity    | Track growth, storage consumption, and projected capacity issues                                                                  |
| Migrations  | Inspect migration status, identify likely conflicts, and propose a migration plan                                                 |
| Recovery    | Prepare recovery procedures and run restore tests in an isolated environment                                                      |

It should answer questions such as:

- “How much storage is the `TEKEYE(PESHAWER)` database using?”
- “Which tables consume the most space?”
- “Why is PostgreSQL using too many connections?”
- “Did last night's backup succeed?”
- “Can this migration be applied without risking production data?”

Destructive queries, schema changes, restores, and production migrations must require explicit authorization and appropriate safeguards.

## 5. Web servers, applications, and API troubleshooting

This is one of the most important capabilities for a practical IT specialist.

### Web infrastructure

- Nginx and Apache configuration and service diagnostics.
- Reverse proxy, upstream, gateway, and timeout errors.
- SSL/TLS certificate validity and renewal problems.
- HTTP 400, 401, 403, 404, 500, 502, 503, and 504 diagnosis.
- DNS, domain, virtual host, and redirect problems.
- Static file permissions and frontend deployment issues.

### Application stacks

- Django, Django REST Framework, Gunicorn, and Uvicorn.
- React, Vite, Next.js, Node.js, and npm.
- Python virtual environments and dependency conflicts.
- REST API authentication, permissions, request failures, and latency.
- Environment variables and missing configuration.
- Git branches, commits, merge conflicts, and deployment differences.
- Service-to-service connectivity and application dependency mapping.

### Deployment diagnosis

For a deployment problem, the agent should be able to:

1. Identify the relevant application and host.
2. Check its service status and recent logs.
3. Inspect available resources and dependencies.
4. Compare the deployed version with the intended release.
5. Identify the likely cause using collected evidence.
6. Propose a fix or rollback plan.
7. Request approval before changing production.
8. Verify the result after the approved action.

For example, if your Django backend fails because its virtual environment is missing Django, the agent should identify the Python interpreter used by the service, inspect dependency configuration, and propose a safe correction. It should not blindly install packages into the system Python.

## 6. Cybersecurity and system auditing

The IT specialist should support defensive security operations, with a clear distinction between finding a problem and changing a system.

Security posture assessment

Inspect exposed services, SSH configuration, excessive permissions, insecure configurations, outdated packages, and risky defaults.

Identity and access review

Review users, groups, sudo privileges, service accounts, authentication failures, and excessive access.

Vulnerability and exposure checks

Identify outdated software and known vulnerabilities, review exposed ports, and prioritize findings by severity and exposure.

Incident investigation

Correlate authentication events, application logs, service changes, and network evidence into a timeline.

Compliance and audit reporting

Record findings, evidence, affected assets, remediation recommendations, and outstanding risks.

Further capabilities can include file integrity monitoring, suspicious process detection, brute-force indicators, certificate expiry monitoring, and security baseline comparisons.

The agent should not claim that a server is secure simply because a limited scan found no problems. It must explain the scope of the assessment and any checks that were not performed.

## 7. Safe remote command execution

This is the core feature that transforms MulteAgent's IT specialist from a conversational assistant into an operational agent.

The system should have three distinct operating modes.

Mode 1: Audit

Read-only

The agent may run authorized diagnostic commands and collect evidence. No system changes are allowed.

Mode 2: Operate with confirmation

The agent prepares a proposed change, shows the exact target and action, explains risks, and waits for an authorized user to approve it.

Mode 3: Operate with trusted policies

The agent can automatically execute only narrowly defined, preapproved actions on specifically authorized servers. All other changes still require approval.

### Remote execution features

- Register, edit, test, disable, and remove server connections.
- Use your existing server vault and SSH sessions.
- Maintain a per-server permission policy.
- Validate commands against structured allowlists.
- Enforce connection timeouts, execution timeouts, and output-size limits.
- Capture stdout, stderr, exit codes, and execution duration.
- Stream tool calls and results into the existing job/SSE event stream.
- Maintain a durable audit trail of requests, approvals, actions, and outcomes.
- Cancel supported running tasks and handle disconnected sessions safely.
- Use separate low-privilege accounts instead of unrestricted root access.
- Prevent shell injection, command substitution, unauthorized host access, and privilege escalation.
- Require stronger approval for restarts, firewall changes, package installation, deployments, and database modifications.

Critical distinction: an LLM prompt is not a security boundary. Enforce authorization and command policies in application code and on the execution side.

## 8. Monitoring, alerting, and incident response

The agent should progress from answering questions on demand to helping detect problems proactively.

| Capability            | Expected behavior                                                                             |
| --------------------- | --------------------------------------------------------------------------------------------- |
| Health checks         | Track service reachability, server resources, database connectivity, and configured endpoints |
| Threshold alerts      | Detect high CPU, low disk space, memory pressure, excessive errors, and certificate expiry    |
| Log analysis          | Group repeated errors and identify unusual changes                                            |
| Incident correlation  | Relate a service failure to resource pressure, a recent deployment, or a dependency failure   |
| Incident timeline     | Record observations, timestamps, tool calls, and state changes                                |
| Notifications         | Send alerts through configured email, Telegram, or other supported integrations               |
| Scheduled audits      | Run approved read-only checks on a schedule                                                   |
| Recovery verification | Verify that a service or endpoint is healthy after a permitted remediation                    |
| Escalation            | Notify a human when evidence is insufficient, risk is high, or recovery fails                 |

Monitoring must use a scheduler, event source, or monitoring integration. The LLM should not be expected to watch servers continuously by itself.

## 9. AI reasoning and multi-agent intelligence

MulteAgent already has a semantic router and specialist architecture. The IT specialist should use those components intelligently instead of creating a separate agent for every command.

### Core intelligence features

- Intent classification: distinguish a general question, a troubleshooting request, an audit, and a request to change a system.
- Tool selection: choose between `network_scan`, `security_check`, `server_audit`, and `run_on_server` based on the task.
- Evidence-based diagnosis: collect data before concluding what caused a problem.
- Multi-step investigations: plan a sequence of safe checks and adapt based on their results.
- Root-cause analysis: distinguish observed symptoms from likely causes and confirmed causes.
- Knowledge retrieval: search internal documentation, runbooks, architecture notes, previous incidents, and relevant configurations.
- Citations: reference the logs, tool outputs, documentation, and timestamps supporting a conclusion.
- Uncertainty handling: say when evidence is missing or conflicting.
- Persistent incident context: remember relevant findings across messages and investigation steps.
- Specialist collaboration: request help from the Development specialist for code problems or another specialist when the task crosses domains.
- Final verification: check whether the proposed fix actually resolved the reported issue.

### Example

A user asks, “Why is my website returning 502?”

The IT specialist should identify the affected host and website, inspect Nginx, test the upstream application, review recent logs, check resource pressure, and then explain the likely cause with evidence. It should propose an appropriate remediation and request approval if the solution changes production.

It should not automatically restart every service on the host just because the site is unavailable.

## 10. Virtualization, storage, and hardware management

Add support for the physical and virtual infrastructure your IT team manages.

- Disk partitions, logical volumes, volume groups, filesystem usage, and inode exhaustion.
- RAID health, SMART information, and hardware alerts where accessible.
- LVM capacity planning and safe expansion plans.
- Virtual machines and hypervisor health through authorized APIs.
- GPU monitoring: utilization, memory, temperature, driver status, and CUDA availability.
- Network adapters, NIC errors, and hardware inventory.
- UPS and power status through supported monitoring interfaces.
- Storage arrays, network shares, and mounted filesystems.
- Capacity forecasts and recommendations for resource allocation.

For example, the agent should be able to investigate why a Linux root filesystem is full and determine whether logs, application files, database growth, or Docker images are responsible before proposing cleanup.

## 11. Backup, disaster recovery, and business continuity

The agent should treat backup verification as a separate capability from backup execution.

- Inventory backup jobs, schedules, retention policies, and destinations.
- Check whether backups completed and whether their sizes are plausible.
- Detect stale backups, missing snapshots, and failed backup jobs.
- Validate backup integrity where the backup technology supports it.
- Plan database, application, and configuration restoration.
- Run isolated restore tests.
- Document recovery point objectives (RPO) and recovery time objectives (RTO).
- Maintain a recovery runbook for each critical application.
- Prepare disaster-recovery checklists and post-recovery verification.
- Require authorization before overwriting production data or restoring snapshots.

A backup marked “successful” is not proof that a system can be restored. MulteAgent should report the last successful restore test separately.

## 12. IT documentation and knowledge management

Your existing PostgreSQL and pgvector knowledge brain can become the IT specialist's operational knowledge source.

The agent should support:

- Importing internal documentation, Markdown files, PDFs, configuration examples, and runbooks.
- Indexing server inventories, service ownership, architecture diagrams, and dependency information.
- Retrieving relevant documentation before recommending a fix.
- Generating and maintaining standard operating procedures.
- Creating incident reports, audit reports, maintenance plans, and change requests.
- Recording known issues, workarounds, and verified solutions.
- Versioning runbooks and flagging stale documentation.
- Citing the source document and relevant passage in answers.
- Separating approved organizational knowledge from unverified incident notes.
- Respecting document permissions and row-level security.

The knowledge base should never treat arbitrary log entries or text found on a remote server as trusted instructions. Those are evidence to analyze, not instructions to obey.

## 13. IT operations dashboard

The frontend should make the agent's work transparent. Users should see what it is investigating, what evidence it found, and whether it is allowed to act.

## Server inventory

Hosts, operating systems, connectivity, and ownership

## Health overview

CPU, memory, disk, service health, and alerts

## Investigation chat

Streaming answers, tool calls, and collected evidence

## Approval center

Pending actions, risk explanations, and approval history

## Jobs and runbooks

Task progress, execution results, and scheduled checks

## Audit history

Who requested, approved, and executed each action

Additional dashboard pages should include network inventory, security findings, database health, backups, knowledge sources, agent settings, user permissions, and model/tool configuration.

Use your existing job queue and SSE architecture to show live task status. Users should be able to stop an investigation, inspect tool output, and distinguish a proposed action from an action that actually completed.

## 14. Integrations the IT specialist should support

Build integrations incrementally, with each connector having a defined permission scope.

| Integration          | Purpose                                            |
| -------------------- | -------------------------------------------------- |
| SSH                  | Linux and Unix server diagnostics                  |
| WinRM / PowerShell   | Windows server management                          |
| PostgreSQL           | Database health, storage, and performance          |
| MongoDB              | Database health and operations                     |
| HTTP/HTTPS           | Website, API, and TLS checks                       |
| Git and GitHub       | Source code, deployment history, and changes       |
| Nginx / Apache       | Web server diagnostics                             |
| Docker               | Container status, logs, and resource usage         |
| systemd              | Service status, logs, and lifecycle management     |
| Monitoring platforms | Metrics, alert ingestion, and incident correlation |
| Email / Telegram     | Notifications and incident updates                 |
| Ticketing systems    | Incident creation, assignment, and tracking        |
| Virtualization APIs  | VM inventory and health                            |
| GPU monitoring       | GPU health and AI workload diagnostics             |

MCP-compatible integrations can be useful where they exist, but they should not replace your application's authorization layer or your existing tools when those tools are more suitable.

## 15. Multi-user access and enterprise controls

Since MulteAgent is intended for multiple LAN users, user identity and permissions should apply to every tool call, not just to the chat interface.

- Role-based access control for administrators, IT operators, developers, auditors, and read-only users.
- Per-server and per-action permissions.
- Separate permission levels for reading logs, running diagnostics, and modifying services.
- Approval policies based on user role, server criticality, and action risk.
- Session management, authentication, and revocation.
- Credential storage outside prompts and chat history.
- Secrets redaction in tool output and application logs.
- Durable audit records with access controls.
- Rate limits and resource quotas for expensive operations.
- Tenant or project isolation where needed.
- Retention policies for conversations, logs, and incident evidence.

An administrator should not have to grant unrestricted root access just to enable server diagnostics.

## 16. Advanced features for later releases

These features are valuable, but I would not make them prerequisites for the first operational IT specialist.

- Predictive capacity and anomaly detection.
- Dependency graphs and automated impact analysis.
- Correlation of alerts across multiple servers.
- Change-risk scoring before deployments.
- Automated incident postmortems.
- Configuration drift detection.
- Scheduled patch recommendations.
- Canary deployments and controlled rollbacks.
- Self-healing for narrowly defined, preapproved failure conditions.
- Natural-language infrastructure reports.
- Cost and resource optimization.
- Collaboration between IT, Development, and Security specialists.
- Windows and Linux fleet management through a unified interface.

Autonomous remediation should be introduced only after the underlying actions, authorization, logging, rollback strategy, and verification have been tested.

## 17. Recommended development priority

Do not attempt to implement all these features at once. The following sequence gives MulteAgent real operational capability early while limiting risk.

## P0

Foundation

Validate the current tools and SSH layer

Test `server_audit` against one real Linux server. Fix connection, authorization, timeout, and output handling issues before extending remote execution.

## P1

MVP

Read-only remote diagnosis

Implement `run_on_server`, strict command validation, registered-server enforcement, streaming output, timeouts, output caps, and security tests. Integrate it into the IT specialist.

## P2

Control

Approval and audit framework

Add pending actions, server-side approvals, role checks, operating modes, and tamper-resistant audit logging before enabling modifications.

## P3

Expansion

Incident intelligence and operations

Add structured diagnosis workflows, incident history, knowledge-backed runbooks, monitoring, alerts, and richer server inventory.

## P4

Advanced

Controlled remediation and broader integrations

Add Windows support, database operations, deployment workflows, backup verification, and policy-controlled autonomous actions.

Your uploaded project notes specifically prioritize validating `server_audit` first, then implementing read-only `run_on_server`, followed by the trust gate and approval interface.&#x20;

Pasted text(20261021-061440).txt

## 18. How to know the IT specialist is ready

Define measurable acceptance tests rather than judging it only by how convincing its answers sound.

- It refuses to access unregistered servers.
- It rejects command-injection attempts and commands outside the allowlist.
- It respects execution timeouts and output limits.
- It correctly identifies service failures from actual logs and status output.
- It distinguishes confirmed facts from hypotheses.
- It cites evidence for its diagnosis.
- It does not claim that a command ran when it did not.
- It cannot execute a production change without the required authorization.
- It records the requester, target server, action, approval, and result.
- It verifies service health after an approved change.
- It handles unavailable servers and conflicting evidence honestly.
- It passes repeatable automated tests before a new tool is enabled.

Track diagnostic accuracy, tool-selection accuracy, false conclusions, unauthorized-action rejection, successful task completion, and time to diagnosis.

## My final recommendation

Build MulteAgent's IT specialist around five core capabilities:

1. Observe: collect trustworthy information from authorized systems.
2. Understand: correlate system state, logs, configuration, and historical incidents.
3. Diagnose: identify likely causes and present evidence-backed findings.
4. Act safely: execute only permitted actions under enforceable authorization policies.
5. Verify: confirm the outcome and document what happened.

That is the right foundation for an IT agent that can grow from read-only troubleshooting into a mature operations platform without needing to rebuild its architecture.

The immediate implementation target should be server diagnostics + evidence-based reasoning + safe tool execution + approval controls. Monitoring, remediation, Windows management, and additional integrations can then extend the same foundation.
