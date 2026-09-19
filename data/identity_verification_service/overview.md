# Identity Verification Service — Architecture Overview

> Synthetic example documentation for a fictional service. It contains no real
> personal data and describes no real verification provider.

## Purpose

Establishes that a customer is who they claim to be, to a configurable level of
assurance, before they are allowed to transact.

## Assurance levels

Verification is not binary. The service returns a level, and each consuming
product decides what it requires.

| Level | Established by | Typical use |
|---|---|---|
| `L0` | Nothing | Browsing |
| `L1` | Verified email or phone | Low-value purchases |
| `L2` | Government ID document check | Standard account opening |
| `L3` | Document check plus liveness-verified selfie match | High-value or regulated flows |

Levels only ever increase within a session. A failed `L3` attempt does not
revoke an existing `L2`.

## Verification flow

1. **Session created.** The client requests a session for a target level. The
   service returns the ordered list of steps required to reach it.
2. **Steps completed.** Each step is submitted independently. Steps can be
   completed out of order where they have no dependency.
3. **Evaluation.** Once every required step has a result, a decision engine
   combines them into an outcome: `APPROVED`, `REJECTED`, or `MANUAL_REVIEW`.
4. **Notification.** The outcome is published as an event, and the session
   becomes read-only.

Sessions expire after 15 minutes of inactivity. An expired session cannot be
resumed; the client starts a new one, and previously completed steps are not
re-used, because a stale document scan is not evidence about the person in
front of you now.

## Step types

- **Document check.** An image of an identity document is submitted. The
  service extracts fields, validates the document's security features, and
  checks the expiry date.
- **Selfie match.** A live capture is compared against the document photo. A
  similarity score above the configured threshold passes.
- **Liveness.** Establishes that the selfie is of a present person rather than
  a photograph or a replay.
- **Data match.** Submitted attributes are compared with the document fields.
  Mismatch in name or date of birth fails the step.

## Decision engine

Rules are declarative and versioned. Each rule has a condition and an outcome,
evaluated in priority order; the first match wins, and the matched rule id is
recorded on the decision so any outcome can be explained after the fact.

Default rules:

| Priority | Condition | Outcome |
|---|---|---|
| 10 | Document expired | `REJECTED` |
| 20 | Liveness failed | `REJECTED` |
| 30 | Selfie similarity below the hard floor | `REJECTED` |
| 40 | Selfie similarity between the floor and the threshold | `MANUAL_REVIEW` |
| 50 | Any data mismatch | `MANUAL_REVIEW` |
| 99 | Otherwise | `APPROVED` |

Thresholds are configuration, not code. Changing one is a config change with an
audit record, because a threshold change alters who is admitted and must be
attributable.

## Manual review

`MANUAL_REVIEW` puts the session into a queue. Reviewers see the submitted
evidence, the rule that fired, and the scores. A reviewer's decision is final
for that session and is recorded with the reviewer's id and a timestamp.

Queue items age out after 48 hours into a `REJECTED` outcome with reason
`REVIEW_TIMEOUT`, so a customer is never left waiting indefinitely.

## Data handling

- Document images and selfies are stored encrypted and deleted after the
  retention period configured for the jurisdiction.
- Extracted fields are stored separately from images, so a service that needs a
  date of birth never gains access to the underlying scan.
- Every read of verification evidence is audit-logged with the accessing
  principal.
- Verification evidence is never included in events. Events carry the outcome
  and the session id only.

## Published events

| Topic | Meaning |
|---|---|
| `identity.session_created` | A verification session has started |
| `identity.verified` | Session reached an approved outcome |
| `identity.rejected` | Session was rejected |
| `identity.review_required` | Session routed to manual review |
