# Payment Service — Architecture Overview

> Synthetic example documentation for a fictional service.

## Purpose

The Payment Service authorises, captures, and refunds money for an order. It
abstracts over several payment providers so the rest of the platform never
learns which provider handled a given transaction.

## Provider abstraction

Each provider is implemented behind a `PaymentProvider` interface with four
operations: `authorize`, `capture`, `refund`, `void`. Provider-specific error
codes are normalised into a shared taxonomy before they leave the service, so a
caller never branches on a provider's vocabulary.

Provider selection is rule-based, evaluated in order:

1. Currency support — a provider that cannot settle the order currency is out.
2. Instrument support — card, wallet, or bank transfer.
3. Health — providers whose circuit breaker is open are skipped.
4. Cost — cheapest remaining provider wins.

If every provider is filtered out, the service returns `503 NO_PROVIDER_AVAILABLE`
rather than queuing, because a customer waiting at a checkout page needs an
answer.

## Authorize-then-capture

Payments are two-phase by default:

- **Authorize** places a hold on the customer's funds and returns an
  `authorization_id`. It does not move money.
- **Capture** converts a hold into a settled charge. It can be for the full
  authorised amount or less.

Holds expire after 7 days. An authorisation that is never captured is voided by
a daily job, and the Order Service is notified so it can cancel the order.

Single-phase (`auto_capture: true`) is available for flows where fulfilment is
instant, and is the wrong choice for anything that ships physically: you do not
want to have taken money for an item you then discover you cannot send.

## Idempotency and retries

Every provider call carries an idempotency key derived from
`(order_id, attempt_number, operation)`. Retries reuse the key, so a retried
capture after a network timeout settles at most once.

Retry policy: 3 attempts, exponential backoff with jitter (1s, 2s, 4s ±25%).
Only transport errors and explicitly retryable provider codes are retried; a
declined card is a final answer and is never retried automatically.

## Circuit breaker

Per provider, the breaker opens after 5 consecutive failures or a 50% error
rate over a 30-second window. Open for 60 seconds, then half-open: a single
probe request decides whether to close or re-open. Breaker state is local to
each instance, which means a partial outage degrades gradually rather than all
instances failing over at once.

## Refunds

A refund references the original capture. Partial refunds are supported and may
be issued repeatedly until the captured total is exhausted. Refunds are
asynchronous — providers take between seconds and several business days — so
the service acknowledges the request, emits `payment.refund_pending`, and emits
`payment.refunded` when the provider confirms.

Refunding more than was captured returns `422 REFUND_EXCEEDS_CAPTURE`.

## Reconciliation

Every night the service pulls a settlement file from each provider and compares
it against its own ledger. Discrepancies are written to a `reconciliation_breaks`
table for manual review rather than auto-corrected: silently rewriting a
financial ledger to match an external file is how you lose the ability to
explain your own numbers.

## Published events

| Topic | Meaning |
|---|---|
| `payment.authorized` | Hold placed |
| `payment.captured` | Money settled |
| `payment.failed` | Authorisation or capture declined |
| `payment.refund_pending` | Refund accepted by the provider |
| `payment.refunded` | Refund confirmed settled |

## Security notes

Card data never reaches this service. Clients tokenise the instrument in the
provider's own SDK and send only the token, which keeps raw card numbers out of
the application's logs, database, and memory entirely.
