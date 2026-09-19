# Notification Service — Architecture Overview

> Synthetic example documentation for a fictional service.

## Purpose

Delivers messages to customers over email, SMS and push, from templates, in
response to domain events. It is the only service permitted to contact
customers directly, which means every message is subject to one consistent set
of preference and rate rules.

## Event-driven by default

Most notifications are not requested; they are *derived*. The service
subscribes to domain events from other services and maps them to templates via
a routing table:

| Event | Template | Channels |
|---|---|---|
| `orders.created` | `order_confirmation` | email |
| `orders.paid` | `payment_receipt` | email |
| `orders.cancelled` | `order_cancelled` | email, push |
| `payment.failed` | `payment_failed` | email, sms, push |
| `identity.review_required` | `verification_pending` | email |

This keeps senders ignorant of channels and templates. The Order Service does
not know that a cancellation sends a push notification; it only knows that an
order was cancelled.

A direct `POST /v1/notifications` endpoint exists for one-off operational
messages and is rate-limited far more aggressively than the event path.

## Templates

Templates are versioned, stored centrally, and rendered with a strict engine:
an unknown variable is an error at render time, not an empty string. A template
has one body per channel, since a 160-character SMS and an HTML email are not
the same message.

Localisation is by template variant. If a variant for the customer's locale is
missing, the service falls back to the default locale and records the miss as a
metric rather than failing the send.

## Delivery pipeline

```
event → route → render → preference check → rate check → dispatch → track
```

Each stage can reject. A rejection is a terminal, recorded outcome, not an
error — "suppressed because the customer opted out" is a successful execution
of the pipeline.

## Preferences and suppression

Customers can opt out per category (`transactional`, `marketing`, `security`)
and per channel. Transactional and security messages cannot be disabled
entirely, only redirected to another channel.

A suppression list holds addresses that hard-bounced, marked a message as spam,
or were explicitly unsubscribed. The list is checked before every dispatch and
is never bypassed, including by the direct endpoint.

## Rate limiting

Per customer, per category: at most 5 messages an hour and 20 a day. Bursts
beyond the limit are dropped, not queued, and counted in
`notifications_rate_limited_total`. Queuing would mean a customer receiving a
flood of stale messages hours later, which is worse than not receiving them.

## Retries and dead-lettering

Dispatch failures retry 3 times with exponential backoff. Provider responses
are classified first:

- **Permanent** (invalid address, hard bounce): no retry; the address is added
  to the suppression list.
- **Transient** (provider 5xx, timeout, throttle): retried.

After the final attempt the message goes to a dead-letter queue with the full
context needed to replay it.

## Delivery tracking

Provider webhooks update delivery state: `queued → sent → delivered`, with
`bounced`, `failed` and `complained` as terminal alternatives. Webhooks arrive
out of order, so state transitions are guarded — a `sent` callback arriving
after `delivered` is ignored rather than regressing the state.
