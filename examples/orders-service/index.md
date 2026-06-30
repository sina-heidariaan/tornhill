---
title: orders-service — architecture blueprint (example)
summary: Synthetic example showing the aerial output format. Not a real codebase.
derived-from: a1b2c3d
generated: example
---

# orders-service — architecture blueprint (example)

> Synthetic demo of the aerial format. The diagrams, flow, and findings below are
> illustrative — in a real run every node and finding cites a real `path:line`.
> Click links resolve against the analyzed repo (here they are placeholders).

## L1 — System context

```mermaid
flowchart LR
  client[Web / Mobile clients]:::ext
  api[orders-service API]:::sys
  worker[orders-service worker]:::sys
  db[(PostgreSQL)]:::data
  mq[(RabbitMQ)]:::data
  pay[Payment provider]:::ext
  client -->|REST| api
  api --> db
  api -->|publish| mq --> worker
  worker --> db
  api -->|charge| pay
  classDef sys fill:#dbeafe,stroke:#2563eb;
  classDef data fill:#e9ecef,stroke:#868e96;
  classDef ext fill:#f8f9fa,stroke:#adb5bd;
```

## L3 — Components

```mermaid
flowchart TB
  app[app entrypoint]
  ord[orders]:::hot
  cart[cart]
  pay[payments]
  inv[inventory]
  app --> ord & cart & pay & inv
  ord --> pay & inv
  click ord href "src/orders/orders.service.ts"
  click pay href "src/payments/payments.service.ts"
  classDef hot fill:#ffe3e3,stroke:#cf222e,stroke-width:2px;
```

## Findings

- **[high]** `orders` is a churn × centrality hotspot — high commit churn AND
  high fan-in (cart, payments, inventory all route through it). Decompose by
  responsibility. _Evidence: 88 churn touches; in-degree 3._
- **[medium]** **Hidden coupling**: `payments.service` co-changes with
  `inventory.reservation` in 6 commits despite no structural link — a checkout
  invariant is split across two modules. _Evidence: co_change confidence 0.85._
- **[medium]** Checkout flow charges the payment provider before the DB
  transaction commits — a failure between the two leaves a charge with no order.
  _Evidence: see flow below._

## Flows

### Checkout

```mermaid
sequenceDiagram
  participant C as Client
  participant O as orders.service
  participant P as payments
  participant Pay as Payment provider
  participant DB as PostgreSQL
  C->>O: POST /checkout
  O->>P: charge
  P->>Pay: charge card
  Pay-->>P: ok
  O->>DB: insert order
  Note over O,DB: edge case — charge succeeds, DB insert fails → orphan charge
```
