Fix: trim trailing whitespace from CSV cells when importing customer records.
Current behavior: cells like "  alice@example.com  " are stored verbatim,
breaking downstream email matching. Expected: leading and trailing whitespace
removed before storage. Internal whitespace preserved.
