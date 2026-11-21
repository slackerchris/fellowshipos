# Repository guidance

Read [PROJECTS.md](PROJECTS.md) for the product map, builds, and Git workflow.

FellowshipNAS is its own project in the FellowshipOS-NAS repository
(`~/Git/FellowshipOS-NAS`) since 2026-11-20. Do NAS work there, starting from
its release list. Never make a build, import or CI step here depend on that
checkout; a fix wanted in both projects is made in each.

Bring new features and policy changes to Chris before starting them.
