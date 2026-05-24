# LIBS/ED-LIBS Cloud Literature Digest

This folder contains a cloud-ready daily literature digest job. It does not depend on the local Codex app, so it can run while the computer is off.

## What It Does

- Runs every day at 08:00 Beijing time through GitHub Actions.
- Searches public metadata sources: Crossref, OpenAlex, and Semantic Scholar.
- Focuses on LIBS/ED-LIBS heavy-metal detection: Cd/Cr/Pb/Cu, soil/water, enrichment substrates, electrodes, gate delay/gate width, and quantitative modeling.
- Sends a Chinese email digest with 5 papers only.
- Does not include images or attachments.
- Gives each paper one fuller paragraph covering method route, result relevance, project connection, and caveats.

## Deployment

1. Put this workspace into a GitHub repository.
2. Commit these files:
   - `.github/workflows/libs-literature-digest.yml`
   - `cloud_literature_digest/literature_digest.py`
   - `cloud_literature_digest/README.md`
3. In GitHub, open the repository settings:
   - `Settings` -> `Secrets and variables` -> `Actions` -> `New repository secret`
4. Add these secrets:
   - `SMTP_USERNAME`: your Gmail address, for example `f2539705061@gmail.com`
   - `SMTP_PASSWORD`: a Gmail app password, not your normal Gmail login password
   - `DIGEST_TO`: the email address that should receive the digest
5. Optional secret:
   - `SEMANTIC_SCHOLAR_API_KEY`: if omitted, the script skips Semantic Scholar and uses Crossref/OpenAlex only
6. Open `Actions` -> `LIBS Literature Digest` -> `Run workflow` to test it manually.

## Gmail App Password

Gmail SMTP usually requires a Google app password. The normal Gmail account password should not be used here. Create an app password in the Google account security settings, then store it only as the GitHub secret `SMTP_PASSWORD`.

## Local Dry Run

From this workspace:

```powershell
python .\cloud_literature_digest\literature_digest.py --dry-run
```

To test email sending locally, create a `.env` file in the workspace root:

```env
SMTP_USERNAME=your-gmail-address@gmail.com
SMTP_PASSWORD=your-google-app-password
DIGEST_TO=your-gmail-address@gmail.com
```

Then run:

```powershell
python .\cloud_literature_digest\literature_digest.py --send
```

Do not commit `.env`.

## Notes

- GitHub Actions scheduled jobs are not guaranteed to run at the exact second, but they are suitable for daily email delivery.
- The script uses public metadata and abstracts. It will not claim full-text reading unless the implementation is later extended to fetch and parse open full text.
- If fewer than 5 high-relevance papers are found in the configured lookback window, the script sends fewer papers rather than filling the digest with weakly related results.
