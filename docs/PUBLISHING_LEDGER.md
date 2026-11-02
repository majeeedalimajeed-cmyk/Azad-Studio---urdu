# Facebook publishing safety ledger

Automatic Facebook publication is gated by a durable ledger in `publication-ledger.json` on the repository's `publishing-ledger` branch, separate from the scheduled workflow branch and the GitHub Actions cache. It is required for every live card upload. Do not delete, reset, force-push, or overwrite that branch. The two manually published cards have been seeded as confirmed records.

Before an upload, the publisher reads and validates the ledger, checks the article URL and similar recent headlines, and atomically writes a `reserved` record using the GitHub Contents API's blob-SHA compare-and-swap. If another writer wins the update, it rereads and checks again. If the ledger is unavailable, corrupt, conflicting, or cannot be written, the upload is held. A lost response to the ledger write also holds the upload; a reservation that actually committed blocks the next attempt.

After Facebook returns a post ID, the publisher marks the reservation `confirmed`. If an upload times out, produces an unconfirmed response, or errors, the claim remains blocked and is recorded as `uncertain` or `rejected` when possible. **No ambiguous upload is automatically retried**, even after the runner or cached SQLite database is lost. A failed outcome update leaves the initial reservation in place. Check the Page manually before any operator-authorized intervention. Do not delete a reservation merely because a feed query returns no result: this Page's feed access has returned a permission error.

The ledger uses exact canonical URLs and a conservative recent-headline similarity rule; it cannot guarantee semantic duplicate detection across every differently worded source. Editorial duplicate review remains necessary. Legitimate similar stories can be held for review. The ledger does not replace factual verification, Page identity checks, or the approved-card/hash gates.

The hourly workflow uses its ephemeral `GITHUB_TOKEN` with repository `contents: write` permission. Manual workflow dispatch performs a harmless write probe before the scan; it does not publish. Normal publication also fails closed if actual ledger access is unavailable. If the permission or branch protection changes, restore authorized access before attempting any live posting.

Keep `NR_AUTO_POST_ENABLED=false` until editorial and source checks are proven operational. YouTube, video, Instagram and automatic stock-photo selection remain disabled in the cards workflow.
