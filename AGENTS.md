# Repository guidance

Read [PROJECTS.md](PROJECTS.md) for the product map, builds, and Git workflow.

This repository builds FellowshipOS Multi-purpose only. FellowshipNAS is its
own project in the FellowshipOS-NAS repository (`~/Git/FellowshipOS-NAS`) since
2026-11-20; do NAS work there, starting from its release list. FellowshipOS
Desktop, the Rust desktop and its image, is in the fellowship-desktop
repository (`~/Git/fellowship-desktop`) since 2026-11-23; do desktop and
Desktop-image work there. Never make a build, import or CI step here depend on
either checkout; a fix wanted in more than one project is made in each.

Bring new features and policy changes to Chris before starting them.
