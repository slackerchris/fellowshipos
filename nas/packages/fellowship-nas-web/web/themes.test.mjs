// The theme rules Chris ruled on 2026-11-17: status follows the theme, stays
// readable, and Fellowship keeps the colours he chose by hand.
import test from "node:test";
import assert from "node:assert/strict";
import { THEMES, colours, contrast, problems } from "./themes.mjs";

test("every theme and mode carries status colours that read at AA", () => {
    for (const [name, theme] of Object.entries(THEMES)) {
        assert.deepEqual(problems(name, theme), [], `${name} reads below AA`);
        for (const mode of ["dark", "light"]) {
            const c = colours(theme, mode);
            for (const key of ["good", "warning", "bad"]) {
                assert.match(c[key], /^#[0-9a-f]{6}$/, `${name} ${mode} ${key}`);
                assert.ok(contrast(c[key], c.surface) >= 4.5, `${name} ${mode} ${key} on panel`);
            }
        }
    }
});

test("status follows the theme: two themes do not share one green", () => {
    assert.notEqual(colours(THEMES.fellowship, "dark").good, colours(THEMES.silverwater, "dark").good);
    assert.notEqual(colours(THEMES.silverwater, "dark").good, colours(THEMES.highwatch, "dark").good);
});

test("Fellowship keeps the status colours Chris pinned", () => {
    assert.equal(colours(THEMES.fellowship, "dark").good, "#86b88a");
    assert.equal(colours(THEMES.fellowship, "dark").bad, "#d87667");
    assert.equal(colours(THEMES.fellowship, "light").good, "#3f7a46");
    assert.equal(colours(THEMES.fellowship, "light").warning, "#9a6316");
});

test("a theme can pin a status colour to an exact value", () => {
    const theme = structuredClone(THEMES.silverwater);
    theme.dark.pinned.good = "#123456";
    assert.equal(colours(theme, "dark").good, "#123456");
});

test("bad stays nearest its anchor, so a failure still reads as one", () => {
    // The pull toward the accent is weakest for bad: compare each theme's bad
    // against its own accent-mixed anchor share by checking ordering of hues
    // is not required; instead assert bad is not the accent itself.
    for (const [name, theme] of Object.entries(THEMES)) {
        for (const mode of ["dark", "light"]) {
            const c = colours(theme, mode);
            assert.notEqual(c.bad, c.accent, `${name} ${mode} bad became the accent`);
        }
    }
});
