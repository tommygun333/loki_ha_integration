# Publishing and maintaining the HACS custom repository

Target GitHub repository: `tommygun333/loki_ha_integration` (public). Keep the repository root layout intact.

## Initial publish

1. Create a **new public repository** on GitHub named `loki_ha_integration` under `tommygun333`; leave **Initialize with README**, license, and .gitignore **unchecked** because they're already in this source. Enable Issues. Suggested description: `Read-only Grafana Loki monitoring integration for Home Assistant (HACS)` and topics: `home-assistant`, `hacs`, `loki`, `grafana`, `custom-integration`.
2. Extract the repository source ZIP; in its top-level directory run:

   ```bash
   git init -b main
   git add .
   git commit -m "HACS-ready Grafana Loki Monitor v0.1.3"
   git remote add origin https://github.com/tommygun333/loki_ha_integration.git
   git push -u origin main
   ```

   Authenticate with GitHub on your own computer, e.g. Git Credential Manager or SSH. **Never paste your personal access token in chat or commit it.**
3. Wait until the **Validate** workflow in GitHub Actions completes. Resolve any reported errors before tagging a release.
4. Publish the first release:

   ```bash
   git tag -a v0.1.3 -m "Loki Monitor v0.1.3"
   git push origin v0.1.4
   ```

   The `Release` workflow verifies that tag matches `manifest.json`, runs tests/validators, and creates the GitHub Release. Confirm the Releases page shows `v0.1.3`. (If Actions need approval/enablement in the new repo, complete that in GitHub first.)
5. In HACS: **⋮ → Custom repositories**, paste `https://github.com/tommygun333/loki_ha_integration`, type **Integration**, then Add. Download Grafana Loki Monitor and restart HA. Existing manual installation: do not delete the Loki integration in Devices & services; HACS replaces the same domain folder. Back up `/config` first.

## Future update workflow

1. Edit code, tests, documentation on a branch or on GitHub; open a PR to `main` and check the **Validate** workflow.
2. Bump `version` in `custom_components/loki_monitor/manifest.json` (e.g. `0.1.4`) and keep documentation/changelog updated.
3. Merge to `main`, pull locally, then create and push a matching annotated tag:

   ```bash
   git switch main
   git pull --ff-only
   git tag -a v0.1.4 -m "Loki Monitor v0.1.4"
   git push origin v0.1.4
   ```

4. The tag triggers validation and a GitHub Release. HACS discovers the new release version; apply the update in HACS and restart Home Assistant.

**Important:** Tags alone are not GitHub Releases. The release workflow must finish successfully for HACS to see a new version when using releases. Avoid changing an existing release tag; publish another version instead.

## Runtime compatibility

Preserve the `loki_monitor` domain and config entry structure. Changing the GitHub repository name or version does not itself migrate Home Assistant config entries. Never commit HA `.storage`, tokens, secrets or Loki credentials to GitHub.
