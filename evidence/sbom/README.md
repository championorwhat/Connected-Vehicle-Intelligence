# Software bill of materials

CycloneDX SBOMs of the built images, made on 2026-09-30 with Trivy 0.67.2:

    docker run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy:0.67.2 \
      image --format cyclonedx --quiet prognos/<image>:local > evidence/sbom/prognos-<image>.cdx.json

| File | Image | Contents |
|---|---|---|
| `prognos-api.cdx.json` | API and erasure worker | Debian base packages, Python packages |
| `prognos-stream.cdx.json` | normalizer, detector, sink, planner, radar | same |
| `prognos-simulator.cdx.json` | simulator | same |
| `prognos-web.cdx.json` | dashboard (nginx) | Alpine base packages; the JavaScript bundle is listed from `package-lock.json` instead |

The `ml` image (scorer) is missing: it could not be rebuilt in the environment that made
these files (its base-image packages come from `deb.debian.org`, which that network
blocked). Its Python packages are listed from `uv.lock` in
[docs/open-source.md](../../docs/open-source.md).

The human-readable licence list is generated from these files by
`scripts/gen_licences.py`.
