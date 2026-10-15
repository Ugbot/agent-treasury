# Repo setup (owner steps)

Nothing here has been done automatically: no remote exists, nothing is pushed, Pages is not enabled.

## 1. Fix placeholders
Replace `OWNER` in `site/index.html` (3 links and the quickstart `git clone`) with the real GitHub user or org. Update the `og:image` in `site/index.html` to an absolute URL after Pages is live (`https://OWNER.github.io/agent-treasury/og.svg`); some scrapers ignore relative URLs and many reject SVG, so export `site/og.svg` to a 1200x630 PNG for the social preview.

## 2. Create and push
1. Review `git status`, then commit the work yourself.
2. On GitHub create a new **public** repository `agent-treasury` (no README/licence; the repo has them).
3. `git remote add origin git@github.com:OWNER/agent-treasury.git && git push -u origin main`

## 3. Enable Pages
Settings > Pages > Build and deployment > Source: **GitHub Actions**. Then run the "Deploy site to GitHub Pages" workflow (Actions tab) or push a change under `site/`. Site URL: `https://OWNER.github.io/agent-treasury/`.

## 4. Repository metadata
- Description: "Policy-controlled Stripe test-mode payments for a delivery marketplace (hackathon build in progress)"
- Website: the Pages URL
- Topics: `stripe`, `hackathon`, `ai-agents`, `payments`, `policy`, `dbos`, `cognee`, `goap`, `fastapi`, `react`
- Social preview: Settings > General > Social preview > upload the PNG export of `site/og.svg`.

## 5. Pre-submission checklist
Brief: https://hackathons.briefcase.so/stripe-october-2026/ (submit page: .../submit/)
- [ ] Repository is public and the link works logged out
- [ ] Everything uses Stripe test mode; no live keys or secrets committed (check `.env` is ignored, scan history)
- [ ] Written spending policy is in the repo and referenced in the README
- [ ] Each payment shows an evidence-based explanation (clause, policy version, evidence)
- [ ] README status is accurate and matches what the tests cover; site status wording matches
- [ ] Demo video is 2 minutes or less; replace the TODO block in `site/index.html#video` with a real link or embed
- [ ] Simulated parts (map, insurance, receipts) are labelled as simulated
- [ ] Build-window rule: implementation work happened within the permitted window
- [ ] Submit on the hackathon submit page with the repo URL and video
