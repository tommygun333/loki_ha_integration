# Publishing and maintaining Grafana Loki Monitor (HACS)

Repository: `https://github.com/tommygun333/loki_ha_integration` (public). The repository already exists; keep the `custom_components/loki_monitor` HACS layout intact. The internal domain remains **`loki_monitor`**.

## Before publishing

1. Update integration code, tests and README together. Document changes to the Prometheus counter contract, indexed Alloy `severity`, entity IDs and frontend resources when relevant.
2. Run `python -m pip install -r requirements-dev.txt` and `python -m pytest -q` from the repository root. Verify the **Validate** GitHub Actions workflow and any HACS/hassfest checks it runs.
3. If releasing a new integration version, update `custom_components/loki_monitor/manifest.json` and any version-specific examples. Do **not** change the internal domain or overwrite existing HA config entries just to rename the integration.
4. Keep Home Assistant configuration, `.storage`, real auth tokens, passwords and internal personal data out of Git.

## Publish a new release

From a clean local checkout, after tests have passed and changes are merged to `main`:

```bash
git switch main
git pull --ff-only
# Example only: replace with the version in manifest.json.
git tag -a v0.6.1 -m "Loki Monitor v0.6.1"
git push origin v0.6.1
```

The version in the annotated tag must match `manifest.json` (`v0.6.1` → `0.6.1`). The release workflow validates the tag and publishes a GitHub Release. Verify its success in GitHub Actions and the Releases page; pushing a tag alone does not guarantee that a Release was created.

Documentation-only changes can be committed without changing `manifest.json` or creating a new release tag. HACS normally picks up the published code version, not each README edit.

## HACS installation/update

In Home Assistant: **HACS → Custom repositories → add this GitHub URL as an Integration**, download/update **Grafana Loki Monitor**, and restart Home Assistant. Existing manual installations use the same `/config/custom_components/loki_monitor` directory and HA config-entry domain.

For v0.6.0, the integration requires **both** Loki and Prometheus. Upgrade notes in the README explain that four-per-host counters and old built-in sensor entities were replaced with compact summaries. New frontend resources are:

```text
/loki_monitor/loki-log-viewer-card.js?v=0.6.0
/loki_monitor/loki-monitor-cards.js?v=0.6.0
```

Update the resource URLs after frontend changes so browser caches refresh. Verify the reusable popup button and native viewer on desktop and mobile before tagging a new feature release.
