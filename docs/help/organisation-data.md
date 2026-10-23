---
title: Organisation details
area: graph
summary: Industry, size, location and a logo next to each organisation, looked up by its web address.
covers: enrichment
code: enrichment.py, graph.py, dashboard.html
order: 2
---
## Where they come from

- On a hosted Machine, organisation details are looked up for you, at no cost.
- On your own computer, it's off until you add a key for a look-up service you use, like Apollo, People Data Labs or Hunter. See the [configuration guide](https://github.com/Salesflare/TheMachine/blob/main/docs/configuration.md).

Only an organisation's web address is sent, never your messages or who you know there. A few are looked up at a time, at most 50 a day, and refreshed every 90 days. People get their own look-up: see [Job title and photo](graph.md#job-title-and-photo).

## Fix a wrong one

On the organisation's page:

- **Edit** to change the name, website, LinkedIn, industry, size, location, founding year or description. Your edits stay. Empty a field to use the looked-up value again.
- **Wrong company, clear details** if it picked the wrong company.
- **Look it up again** to fetch fresh details.
