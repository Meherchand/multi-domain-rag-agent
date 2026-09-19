# Workflow Engine — Architecture Overview

> Synthetic example documentation for a fictional service.

## Purpose

Runs multi-step business processes that span several services — onboarding a
customer, fulfilling an order, processing a return — without any one service
having to know the whole process.

## Workflows as graphs

A workflow is a directed acyclic graph of **steps**. Each step names a handler,
its inputs, and its dependencies. The engine runs a step when every dependency
has succeeded, so independent branches run in parallel without the author
arranging it.

```yaml
name: customer_onboarding
version: 3
steps:
  - id: verify_identity
    handler: identity.verify
    inputs: { customer_id: "${input.customer_id}", level: "L2" }

  - id: create_account
    handler: accounts.create
    depends_on: [verify_identity]

  - id: send_welcome
    handler: notifications.send
    depends_on: [create_account]
    inputs: { template: welcome_email }

  - id: provision_wallet
    handler: wallet.provision
    depends_on: [create_account]
```

`send_welcome` and `provision_wallet` both depend only on `create_account`, so
they run concurrently.

## Execution model

Each run has an id and durable state. The engine records the outcome of every
step before starting the next, which is what makes a run resumable: a crashed
worker loses in-flight work, not completed work.

Step outcomes: `PENDING`, `RUNNING`, `SUCCEEDED`, `FAILED`, `SKIPPED`,
`WAITING`.

## Handlers

A handler is a named, registered function with a declared input and output
schema. Handlers must be **idempotent**: the engine guarantees at-least-once
execution, not exactly-once, because exactly-once across a network boundary is
not something an orchestrator can honestly promise. Each invocation receives a
stable `idempotency_token` derived from `(run_id, step_id, attempt_group)`.

## Waiting steps

A step can suspend the run until something external happens — a webhook, a
human approval, a timer. The run is persisted and consumes no resources while
`WAITING`. A signal addressed to `(run_id, step_id)` resumes it.

Waits have a timeout. On expiry the step fails with `WAIT_TIMEOUT` and normal
failure handling applies.

## Failure handling

Per-step retry policy: attempts, backoff, and which errors are retryable. After
the final attempt the step fails, and the workflow's `on_failure` policy
decides what happens:

- `fail_run` (default) — the run stops; completed steps are left as they are.
- `compensate` — registered compensation handlers run in reverse dependency
  order, undoing what succeeded.
- `continue` — the branch is abandoned, independent branches carry on.

Compensation is best-effort and explicitly not a transaction. A compensation
handler that itself fails is recorded and surfaced for manual intervention
rather than retried forever.

## Versioning

Workflow definitions are immutable and versioned. A run is pinned to the
version it started on, so editing a definition never changes the behaviour of
a run already in flight. New runs use the latest version unless one is pinned
explicitly.

## Observability

Every run emits a trace with a span per step. Step inputs and outputs are
recorded, with fields marked `sensitive: true` in the schema redacted before
they are written.

Key metrics: `workflow_run_duration_seconds`, `workflow_step_failures_total`,
`workflow_runs_waiting`, `workflow_compensations_total`.

A run that has been `WAITING` longer than its expected duration is the single
most useful alert this system produces — it usually means an external callback
is not arriving.
