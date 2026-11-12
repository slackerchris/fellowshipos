# FellowshipOS themes

One file per theme. Every part of FellowshipOS that draws in colour reads
these files: the NAS web desktop (`nas/packages/fellowship-nas-web/web/themes.mjs`,
at build time), the graphical installer (Fellowship only), and the Rust
desktop for the desktop role (`fellowship-theme` in the fellowship-desktop
repository, at run time). Installed systems get them in
`/usr/share/fellowship/themes`.

Fellowship (`fellowship.toml`) is the default and must exist.

## A theme file

```toml
name = "Silverwater"   # shown in theme pickers
order = 2              # where it sits in pickers; Fellowship is always first

[dark]
window = "#192B3B"     # the desktop and window background
panel = "#243C50"      # panels, dialogs, the launcher
text = "#E9F3FA"
accent = "#9AC8E7"     # buttons, selection, the gold-style highlights
wallpaper = "silverwater.webp"   # in wallpapers/

[light]
...the same five keys...

# Optional: exact values for worked-out colours
[dark.pinned]
raised = "#29392F"
```

Colours are `#RRGGBB`. The files use only `[tables]`, `key = "string"`,
`key = number` and `#` comments, so both readers can stay small.

## Worked-out colours

Each mode's colours come from its four, with `mix(a, b, s)` meaning `a` with
share `s` of `b` mixed in (sRGB, rounded):

| Colour | Dark mode | Light mode |
| --- | --- | --- |
| desktop, bg | window | window |
| surface | panel | panel |
| surface-sunk | mix(window, panel, 0.5) | mix(window, text, 0.04) |
| raised | mix(panel, text, 0.09) | mix(window, text, 0.09) |
| border | mix(panel, text, 0.22) | mix(window, text, 0.22) |
| text | text | text |
| muted | mix(text, panel, 0.3) | mix(text, panel, 0.22) |
| accent, gold | accent | accent |
| accent-text | window | panel |
| icon-label | text | text |

The taskbar and app tiles are dark in either mode, from the dark colours:

| Colour | Value |
| --- | --- |
| bar | dark window |
| bar-text | dark text |
| bar-muted | mix(dark text, dark window, 0.3) |
| bar-border | mix(dark panel, dark text, 0.22) |
| bar-hover | dark panel |
| bar-pressed | mix(dark panel, dark text, 0.09) |
| bar-accent | dark accent |
| tile-top | mix(dark panel, dark text, 0.12) |
| tile-bottom | dark panel |

## Pins

`[dark.pinned]` and `[light.pinned]` set any colour in the tables above to an
exact value. Fellowship pins the colours Chris chose by hand (moss charcoal,
weathered green, muted linen and the rest). Taskbar and tile pins go in
`[dark.pinned]` and apply in both modes.

## Readability

A theme doesn't build if any of these read below 4.5:1 (WCAG AA), in either
mode: text and muted on panel and on window, accent-text on accent, and the
taskbar's text and muted text on the bar.
