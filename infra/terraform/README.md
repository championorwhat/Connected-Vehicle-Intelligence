# Not built yet (M16, planned)

The cloud deployment (Helm chart, Kubernetes manifests, Terraform for one cloud) is
**planned and not built**. It was deliberately scheduled last and did not fit before the
submission deadline.

What exists today: the whole system runs with Docker Compose (`docker-compose.yml`), with
the same images a cluster would run; a 3-broker Kafka overlay is in `infra/docker/`. The
intended deployment is described in the
[Solution Document §5.4](../../docs/solution-document/solution-document.md#54-deployment-view).
