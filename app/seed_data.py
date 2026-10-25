"""Demo evidence for a fictional invoicing product, so the loop can be tried in one click."""

SEED_PRODUCT = {
    "name": "Ledgerly",
    "overview": (
        "Ledgerly is invoicing and light accounting software for freelancers and small agencies. "
        "It sends invoices, tracks payments and syncs with QuickBooks so owners spend less time on admin."
    ),
    "canvas": {
        "customer_segments": "Freelancers and agencies with 1 to 15 people\nOwner-operators who do their own bookkeeping",
        "problem": "Month-end reporting is manual\nAccounting sync is not trusted\nSetup takes too long",
        "unique_value": "Get paid faster with books that stay correct without weekly cleanup",
        "solution": "Invoicing, payment tracking, reports and a two-way QuickBooks sync",
        "channels": "Search for invoicing templates\nAccountant referrals\nFreelancer communities",
        "key_metrics": "Time to first invoice\nSupport tickets per account\nMonthly retained accounts",
        "revenue": "Per-seat subscription\nPayment processing fees",
        "costs": "Payment processing\nHosting\nSupport staff",
        "unfair_advantage": "Direct access to accountants who recommend it to their clients",
    },
}

SEED_EVIDENCE = [
    # Month-end reporting pain
    ("interview", "Every month I export six separate CSVs from the reports page and stitch them together in Excel before I can close the books. It takes me half a day."),
    ("support", "Is there any way to download all my reports at once? I have to click export on each one and rename the files by hand."),
    ("interview", "Month-end close is the worst week of the year. I rebuild the same summary report manually because the export only gives me one report at a time."),
    ("support", "Please add bulk export. Our finance admin spends hours merging exported report files every month."),
    ("review", "Love the invoicing, but the reporting export is painful. No way to get a combined month-end view without spreadsheet gymnastics."),
    ("interview", "My accountant keeps asking for a single file with revenue, outstanding invoices and taxes. I send her five exports and she has to merge them."),
    # QuickBooks sync
    ("support", "The QuickBooks sync duplicated 40 invoices after I reconnected. Now my books do not match and I cannot tell which are real."),
    ("interview", "I do not trust the accounting sync. Twice it has pushed payments to the wrong customer, so I double check everything by hand."),
    ("support", "QuickBooks sync failed silently overnight. I only found out when a client said their payment had not been recorded."),
    ("review", "Sync with QuickBooks is flaky. Invoices show as unpaid in one system and paid in the other."),
    ("support", "How do I see what the sync actually changed? There is no log, so when numbers are off I have no way to debug it."),
    # Onboarding
    ("interview", "I signed up, saw an empty dashboard and had no idea where to start. I almost left before I found the invoice template setup."),
    ("support", "New to the app. Where do I add my logo and payment terms? The setup steps are scattered across three settings pages."),
    ("review", "Took me an hour to send my first invoice. A guided setup would have saved me a lot of confusion."),
    ("interview", "When I onboarded my two employees I could not tell which settings they could see. Setup felt like guesswork."),
    ("support", "Cannot find where to import my existing clients. Went through the help docs twice."),
    # Notifications
    ("review", "I get an email for every single invoice view, reminder and payment. My inbox is flooded and I started ignoring all of them, including the important ones."),
    ("support", "How do I turn off notifications for viewed invoices? I only want to know about payments and failures."),
    ("interview", "Too many alerts. I missed a failed payment notice because it looked like all the other noise."),
    # Dashboard performance
    ("support", "The dashboard takes over 15 seconds to load for accounts with more than a year of invoices."),
    ("review", "Great features but the app gets slower every month as we add data. Filtering invoices sometimes freezes the page."),
    ("interview", "Searching old invoices is so slow I keep a spreadsheet on the side instead."),
    # Recurring invoices
    ("support", "I need recurring invoices that adjust automatically when the hours change. Right now I edit every one manually each month."),
    ("interview", "Half of my clients are on retainers with slightly different amounts monthly. I wish recurring invoices could pull the amount from a template with variables."),
]
