# Order Service — API Reference

> Synthetic example documentation. All identifiers below are fictional.

Base URL: `${ORDER_SERVICE_URL}` (for example `http://localhost:9001`)
Authentication: `Authorization: Bearer <token>` on every endpoint.

## POST /v1/orders

Create an order.

**Headers**

| Header | Required | Notes |
|---|---|---|
| `Idempotency-Key` | yes | Client-generated UUID, unique per logical order |
| `Authorization` | yes | Bearer token |

**Request**

```json
{
  "customer_id": "cust-demo-0042",
  "currency": "USD",
  "items": [
    { "sku": "SKU-DEMO-1", "quantity": 2, "unit_price_cents": 1999 },
    { "sku": "SKU-DEMO-7", "quantity": 1, "unit_price_cents": 4500 }
  ],
  "shipping_address_id": "addr-demo-9"
}
```

**Response** `201 Created`

```json
{
  "order_id": "demo-order-001",
  "status": "PENDING_PAYMENT",
  "total_cents": 8498,
  "currency": "USD",
  "reservation_expires_at": "2024-01-01T12:30:00Z",
  "version": 1
}
```

**Errors**

| Status | Code | Meaning |
|---|---|---|
| 400 | `MISSING_IDEMPOTENCY_KEY` | Header absent |
| 409 | `INSUFFICIENT_INVENTORY` | One or more SKUs could not be reserved |
| 422 | `IDEMPOTENCY_KEY_REUSED` | Key seen before with a different body |
| 424 | `CUSTOMER_NOT_VERIFIED` | Identity Verification Service has not cleared the customer |

## GET /v1/orders/{order_id}

Returns the current order, including its line items and status history.

```json
{
  "order_id": "demo-order-001",
  "status": "PAID",
  "version": 3,
  "history": [
    { "status": "PENDING_PAYMENT", "at": "2024-01-01T12:00:00Z" },
    { "status": "PAID", "at": "2024-01-01T12:04:12Z" }
  ]
}
```

`404 ORDER_NOT_FOUND` if the id is unknown or belongs to another customer —
the two cases are deliberately indistinguishable, so the endpoint cannot be
used to probe for valid order ids.

## POST /v1/orders/{order_id}/cancel

Cancel an order. Legal only from `DRAFT` or `PENDING_PAYMENT`; a `PAID` order
must go through the refund flow instead.

```json
{ "reason": "CUSTOMER_REQUESTED" }
```

Returns `409 INVALID_TRANSITION` with the current status when the order is past
the cancellable states.

## POST /v1/orders/{order_id}/refunds

Request a refund. Accepted only within the 30-day returns window and only for
`PAID`, `COMPLETED` or `PARTIALLY_SHIPPED` orders.

```json
{ "amount_cents": 1999, "reason": "ITEM_DAMAGED", "line_item_id": "li-demo-1" }
```

The order moves to `REFUNDING` immediately and to `REFUNDED` only when the
Payment Service confirms. A partial refund leaves the order in its previous
terminal state with a `refunds` array attached.

## GET /v1/orders?customer_id=&status=&cursor=

Cursor-paginated list, newest first. `limit` defaults to 20 and is capped at
100. The cursor is an opaque string; do not parse it.

## Published events

| Topic | Emitted when |
|---|---|
| `orders.created` | Order accepted and inventory reserved |
| `orders.paid` | Payment captured |
| `orders.cancelled` | Order cancelled, by the customer or by expiry |
| `orders.refund_requested` | Refund accepted for processing |
| `orders.completed` | All line items shipped |

Every event carries `event_id`, `order_id`, `sequence`, `occurred_at`, and a
`payload` object. Consumers must tolerate redelivery.

## Rate limits

600 requests per minute per API token, burst 60. Exceeding the limit returns
`429` with a `Retry-After` header. Writes and reads share the budget.
