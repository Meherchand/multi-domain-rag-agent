# Payment Service — Troubleshooting

> Synthetic example documentation. Error codes and identifiers are fictional.

## A payment is stuck in `PENDING`

Almost always one of three things:

1. **The provider webhook never arrived.** Check the `webhook_events` table for
   the `authorization_id`. If there is no row, replay from the provider
   dashboard, or wait for the reconciliation job to pick it up overnight.
2. **The webhook arrived but failed to process.** Look for the id in the
   dead-letter queue. The usual cause is a schema change on the provider side.
3. **The capture was never issued.** The Order Service issues capture on
   `orders.paid`. If that event was lost, the authorisation sits idle until it
   expires.

Do not manually mark the payment captured. Issue the capture through the API so
the ledger and the provider stay in step.

## `NO_PROVIDER_AVAILABLE` during normal traffic

Every provider was filtered out. Check breaker state on the metrics dashboard
(`payment_provider_breaker_state`). If all breakers are open, the common cause
is an expired credential rather than a provider outage — a 401 from a provider
counts as a failure and trips the breaker like any other.

## Duplicate charges

Should be impossible via the API, because of idempotency keys. When it does
happen, the cause has historically been a client generating a fresh
idempotency key on retry. Check whether the two charges share an `order_id` but
differ in `idempotency_key`; if so, the bug is in the caller.

Resolution is a refund on the duplicate, not a void — the second charge has
already settled by the time anyone notices.

## `REFUND_EXCEEDS_CAPTURE` on a legitimate refund

Usually a partial-refund accounting error: earlier partial refunds are counted
against the same capture. Query the `refunds` table for the capture id and sum
`amount_cents` before assuming the request is wrong.

## High latency on authorisation

The authorise path is a synchronous provider call, so its latency is mostly the
provider's. Check `payment_provider_latency_seconds` broken down by provider
before looking at this service. If one provider is slow, lowering its cost rank
temporarily moves traffic away without a deploy.

## Common error codes

| Code | Retryable | Meaning |
|---|---|---|
| `CARD_DECLINED` | no | Issuer refused the charge |
| `INSUFFICIENT_FUNDS` | no | Self-explanatory; the customer must act |
| `PROVIDER_TIMEOUT` | yes | No answer within the deadline; outcome unknown |
| `PROVIDER_UNAVAILABLE` | yes | Provider returned 5xx |
| `INVALID_TOKEN` | no | Instrument token expired or was already used |
| `NO_PROVIDER_AVAILABLE` | yes | All providers filtered out by routing rules |
| `REFUND_EXCEEDS_CAPTURE` | no | Refund total would exceed the captured amount |

`PROVIDER_TIMEOUT` is the dangerous one: the charge may or may not have
happened. Never retry it with a fresh idempotency key.
