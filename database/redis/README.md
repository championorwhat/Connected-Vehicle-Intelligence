# Redis key conventions (implemented in M5/M6)

Redis holds only rebuildable, short-lived state. It runs with `noeviction`, so state is
never silently dropped; memory pressure fails loudly instead.

| Key | Type | Content | TTL |
|---|---|---|---|
| `veh:{vehicle_id}:state` | hash | latest canonical signals + event_ts | 1 h |
| `dedup:{vehicle_id}` | hash | sequence high-watermark + window bitmap | 1 h |
| `rl:{tenant_id}:{route}` | string | token-bucket counter | window |
| `alerts:{tenant_id}` | pub/sub channel | live alert fan-out to WebSocket servers | n/a |
