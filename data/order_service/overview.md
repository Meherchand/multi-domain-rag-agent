# Order Service — Architecture Overview

> **Synthetic example documentation.** This describes a fictional service and
> exists so the repository has a realistic corpus to index. Replace `data/`
> with your own documentation — see `docs/extending.md`.

## Purpose

The Order Service owns the lifecycle of a customer order, from the moment a
cart is submitted until the order is fulfilled, cancelled, or refunded. It is
the system of record for order state; every other service treats its view of an
order as a cache.

## Responsibilities

- Validate and accept order submissions.
- Hold authoritative order state and enforce legal state transitions.
- Reserve inventory and release reservations when an order fails or expires.
- Emit domain events so downstream services can react.
- Expose order history and status to customer-facing clients.

Explicitly **not** its responsibilities: taking payment (Payment Service),
sending messages to customers (Notification Service), or deciding whether a
customer is allowed to transact (Identity Verification Service).

## State machine

An order moves through a fixed set of states. Transitions not listed here are
rejected with `409 INVALID_TRANSITION`.

| From | To | Trigger |
|---|---|---|
| `DRAFT` | `PENDING_PAYMENT` | Order submitted and inventory reserved |
| `PENDING_PAYMENT` | `PAID` | `payment.captured` event received |
| `PENDING_PAYMENT` | `CANCELLED` | Payment failed, or the 30-minute reservation window expired |
| `PAID` | `FULFILLING` | Warehouse accepted the fulfilment job |
| `FULFILLING` | `COMPLETED` | All line items shipped |
| `PAID` | `REFUNDING` | Refund requested within the returns window |
| `REFUNDING` | `REFUNDED` | `payment.refunded` event received |
| `FULFILLING` | `PARTIALLY_SHIPPED` | Some, but not all, line items shipped |

State is advanced only by a single writer per order, serialised on the order
id. Concurrent transition attempts are resolved by optimistic locking on a
`version` column; the loser retries against the new state.

## Idempotency

Every write endpoint requires an `Idempotency-Key` header. Keys are stored for
24 hours alongside a hash of the request body and the response that was
returned.

- Same key, same body → the stored response is replayed. No new order.
- Same key, different body → `422 IDEMPOTENCY_KEY_REUSED`.
- No key → `400 MISSING_IDEMPOTENCY_KEY`.

This matters because clients retry aggressively on timeout, and an order
service that creates duplicate orders under retry is worse than one that is
occasionally unavailable.

## Inventory reservation

Submitting an order places a soft reservation on each line item for 30 minutes.
Reservations are held in a separate store keyed by `(order_id, sku)` with a TTL.
If payment does not complete in that window, the reservation lapses and the
order is cancelled by a sweeper that runs every minute.

The sweeper is idempotent and safe to run concurrently: it cancels only orders
whose reservation TTL has already expired, and it does so through the same
state machine as any other transition.

## Failure handling

- **Payment Service unreachable**: the order stays in `PENDING_PAYMENT`. It is
  not cancelled on a transport error, because a timeout does not tell you
  whether the charge happened. Resolution comes from the payment event stream
  or from the reservation expiry, whichever arrives first.
- **Event publish failure**: events are written to an outbox table in the same
  transaction as the state change, and relayed by a separate process. State and
  events therefore cannot diverge, at the cost of at-least-once delivery.
- **Duplicate events**: consumers are expected to be idempotent. Every event
  carries an `event_id` and a monotonically increasing `sequence` per order.
